#!/usr/bin/env python3
"""
Compara um punhado de vagas específicas contra o seu CV usando o Jev —
útil quando você já tem alguns links em mãos (ex.: vários processos abertos
na mesma empresa) e quer saber qual tem o melhor encaixe, em vez de rodar o
pipeline inteiro (`screen.py`) que varre centenas de vagas de várias fontes.

Usa exatamente o mesmo setup do screen.py — CV, preferências
(preferencias.txt), perguntas, fórmula de nota e regras de ajuste (ex.:
analista júnior no máximo "avaliar") —, então o resultado é comparável com a
planilha do pipeline principal.

Uso:
    python compare_jobs.py --input bees_compare.json
    python compare_jobs.py --input bees_compare.json --mock   # sem chave/rede, pra testar

Formato do --input (JSON, lista de vagas):
    [
      {
        "titulo": "...",
        "empresa": "...",
        "cidade": "...",
        "estado": "",
        "remoto": false,
        "descricao": "...",
        "link": "https://..."
      },
      ...
    ]
Também aceita os nomes em inglês: title, company, city, state, remote,
description, link.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from jev_client import JevClient, JevError, MockJevClient
import yaml

from screen import RECOMENDACAO_ORDEM, ajustar_recomendacao, build_questions, carregar_preferencias, score_job

REQUIRED_FIELDS = {"titulo", "empresa", "cidade", "estado", "remoto", "descricao", "link"}
# nomes em inglês também valem (ex.: JSON montado à mão ou por outra ferramenta)
ALIASES = {"titulo": "title", "empresa": "company", "cidade": "city", "estado": "state",
           "remoto": "remote", "descricao": "description", "link": "url"}


def _normalize(raw: dict) -> dict:
    job = {k: raw.get(k, raw.get(ALIASES[k], "")) for k in REQUIRED_FIELDS}
    job["remoto"] = bool(job["remoto"])
    job.setdefault("fonte", "manual")
    job.setdefault("id", job["link"] or job["titulo"])
    job.setdefault("publicado_em", "")
    return job


def main():
    # terminal do Windows: sem isso os acentos saem como "Ã§"
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", required=True, help="arquivo JSON com a lista de vagas a comparar")
    ap.add_argument("--cv", default="cv.txt", help="caminho do CV em texto (padrão: cv.txt)")
    ap.add_argument("--model", default="jev-latest")
    ap.add_argument("--mock", action="store_true", help="usa o cliente simulado, sem rede/chave")
    args = ap.parse_args()

    cv_path = Path(args.cv)
    if not cv_path.exists():
        sys.exit(f"CV não encontrado: {cv_path}")
    cv_text = cv_path.read_text(encoding="utf-8")

    raw_jobs = json.loads(Path(args.input).read_text(encoding="utf-8"))
    jobs = [_normalize(j) for j in raw_jobs]
    vazias = [j["link"] or "?" for j in jobs if not j["titulo"] or not j["descricao"]]
    if vazias:
        sys.exit("Vaga sem título ou descrição (confira os nomes dos campos no JSON): " + ", ".join(vazias))

    if args.mock:
        client = MockJevClient(model="jev-mock")
        print("Rodando em modo --mock (sem chamadas reais ao Jev).")
    else:
        try:
            client = JevClient(model=args.model)
        except JevError as e:
            sys.exit(str(e))

    config = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    preferencias = carregar_preferencias(config)
    questions = build_questions(com_preferencias=bool(preferencias))
    results = []
    print(f"Pontuando {len(jobs)} vagas com o Jev...")
    for job in jobs:
        try:
            results.append(ajustar_recomendacao(score_job(client, cv_text, job, questions, preferencias)))
        except JevError as e:
            print(f"  [aviso] falha ao pontuar '{job['titulo']}': {e}", file=sys.stderr)

    # mesma ordem da planilha: recomendação primeiro, depois nota
    results.sort(key=lambda r: (RECOMENDACAO_ORDEM.get(r["recomendacao"], 9), -r["nota_fit"]))

    print("\nComparativo (melhor encaixe primeiro):\n")
    for i, r in enumerate(results, 1):
        print(f"{i}. {r['titulo']} — {r['empresa']}")
        print(f"   Nota de fit: {r['nota_fit']}/10  |  Aderência: {r['aderencia_nivel']}  |  Confiança: {r['confianca']:.0%}")
        print(f"   Senioridade compatível: {r['senioridade_ok']}  |  Gap crítico: {r['gap_critico']}  |  Trilha: {r['trilha']}")
        print(f"   Recomendação: {r['recomendacao']}" + (f"  (ajuste: {r['ajuste']})" if r.get("ajuste") else ""))
        print(f"   {r['link']}\n")


if __name__ == "__main__":
    main()

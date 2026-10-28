#!/usr/bin/env python3
"""
Liga o PC ao painel de candidaturas na nuvem (pasta web/, publicada na Vercel).

Chamado pelo rodar_diario.bat depois da coleta:
1. sobe as vagas "aplicar agora" e "avaliar" do relatório do dia, para a aba
   "Vagas de hoje" (só quando o relatório mudou);
2. baixa as candidaturas registradas no painel para output/candidaturas.json —
   uma cópia de segurança no PC, caso o banco da nuvem se perca.

Uso:
    python sincronizar.py

Precisa de PAINEL_URL (ex.: https://radar-de-vagas.vercel.app) e PAINEL_TOKEN
no .env — o token é o mesmo cadastrado na Vercel.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path

import requests
import yaml

from novidades import _ler_env

# o que a aba "Vagas de hoje" recebe de cada vaga do relatório
CAMPOS_LISTA = ["nota_fit", "recomendacao", "titulo", "empresa", "fonte", "cidade", "remoto",
                "salario", "publicado_em", "link"]


class Nuvem:
    """As rotas /api/pc/* do web/api/index.py."""

    def __init__(self, url: str, token: str):
        self.url = url.rstrip("/")
        self.sessao = requests.Session()
        self.sessao.headers["Authorization"] = f"Bearer {token}"

    def _req(self, metodo: str, caminho: str, **kw) -> dict:
        r = self.sessao.request(metodo, self.url + caminho, timeout=60, **kw)
        if r.status_code == 401:
            raise RuntimeError("a nuvem recusou o PAINEL_TOKEN — confira se é o mesmo do .env e da Vercel.")
        r.raise_for_status()
        return r.json()

    def estado(self) -> dict:
        return self._req("GET", "/api/pc/estado")

    def subir_vagas(self, relatorio: str, vagas: list[dict]) -> None:
        self._req("PUT", "/api/pc/vagas", json={"relatorio": relatorio, "vagas": vagas})


def vagas_do_relatorio(out_dir: Path) -> tuple[str, list[dict]]:
    """(nome do último relatório, vagas "aplicar agora" e "avaliar" dele)."""
    relatorios = sorted(out_dir.glob("vagas_triadas_*.csv"))
    if not relatorios:
        return "", []
    with relatorios[-1].open(encoding="utf-8-sig") as f:
        linhas = list(csv.DictReader(f))
    vagas = []
    for n, v in enumerate(linhas, 1):
        if v.get("recomendacao") not in ("aplicar_agora", "avaliar"):
            continue
        vagas.append({**{k: v.get(k, "") for k in CAMPOS_LISTA}, "n": n,
                      "id": hashlib.md5(v["link"].encode()).hexdigest()[:12]})
    return relatorios[-1].name, vagas


def sincronizar(config: dict, nuvem: Nuvem) -> dict:
    """Uma rodada. Devolve contagens do que fez (para o log)."""
    out_dir = Path(config.get("output_dir", "output"))
    remoto = nuvem.estado()

    relatorio, vagas = vagas_do_relatorio(out_dir)
    subidas = 0
    if relatorio and relatorio != remoto["relatorio"]:
        nuvem.subir_vagas(relatorio, vagas)
        subidas = len(vagas)

    copia = out_dir / "candidaturas.json"
    copia.parent.mkdir(parents=True, exist_ok=True)
    copia.write_text(json.dumps(remoto["candidaturas"], ensure_ascii=False, indent=1), encoding="utf-8")
    return {"vagas": subidas, "candidaturas": len(remoto["candidaturas"])}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args()

    url, token = _ler_env("PAINEL_URL"), _ler_env("PAINEL_TOKEN")
    if not url or not token:
        sys.exit("Faltam PAINEL_URL e PAINEL_TOKEN no .env (ver .env.example) — sem eles não há o que sincronizar.")
    config = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    try:
        r = sincronizar(config, Nuvem(url, token))
    except (requests.RequestException, RuntimeError) as e:
        sys.exit(f"[aviso] não consegui falar com o painel: {e}")
    print(f"Painel: {r['vagas']} vagas do dia enviadas, {r['candidaturas']} candidaturas copiadas "
          f"para output/candidaturas.json.")


if __name__ == "__main__":
    main()

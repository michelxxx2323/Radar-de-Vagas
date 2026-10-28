"""Vagas novas desde a rodada anterior: salva num CSV próprio e manda um aviso.

"Nova" = vaga (empresa + título) que nunca apareceu em nenhuma rodada anterior,
ou seja, que não estava no output/historico.json antes desta rodada.

Aviso no celular via ntfy (https://ntfy.sh, app gratuito, sem conta):
  1. instale o app ntfy no celular e assine um tópico com nome difícil de
     adivinhar (ex.: radar-michel-8f3k2q) — quem souber o nome vê os avisos;
  2. coloque esse nome na variável NTFY_TOPIC (no .env desta pasta ou com
     `setx NTFY_TOPIC radar-michel-8f3k2q`). Fica fora do config.yaml de
     propósito, porque o config vai pro GitHub.
Aviso por e-mail: com NTFY_EMAIL=<seu e-mail> no .env, o ntfy encaminha o
mesmo aviso por e-mail (remetente do ntfy; pode cair no spam nas primeiras
vezes). Desde 2026 o ntfy.sh não manda e-mail anônimo: precisa de conta e de
um token de acesso em NTFY_TOKEN (ntfy.sh > Account > Access tokens).
Se o e-mail falhar, o aviso vai só pro app, sem e-mail.
Sem NTFY_TOPIC, só o CSV é gerado.

Ajustes no config.yaml (opcionais), bloco `avisos`:
  nota_minima: 7.5          # só avisa vagas com nota >= isto
  recomendacoes: [aplicar_agora, avaliar]
"""
from __future__ import annotations

import csv
import datetime as dt
import os
import sys
from pathlib import Path

import requests

COLUNAS = ["recomendacao", "nota_fit", "titulo", "empresa", "cidade", "remoto",
           "trilha", "fonte", "publicado_em", "link"]
MAX_NO_AVISO = 10


def _ler_env(nome: str) -> str | None:
    """Variável de ambiente, ou a linha NOME=valor do .env desta pasta."""
    if os.environ.get(nome):
        return os.environ[nome].strip()
    env = Path(__file__).with_name(".env")
    if env.exists():
        for linha in env.read_text(encoding="utf-8").splitlines():
            if linha.strip().startswith(f"{nome}="):
                valor = linha.split("=", 1)[1].strip().strip('"').strip("'")
                return valor or None
    return None


def filtrar_relevantes(novas: list[dict], config: dict) -> list[dict]:
    cfg = config.get("avisos") or {}
    nota_min = float(cfg.get("nota_minima", 7.5))
    recs = set(cfg.get("recomendacoes", ["aplicar_agora", "avaliar"]))
    rel = [r for r in novas
           if r.get("recomendacao") in recs and float(r.get("nota_fit") or 0) >= nota_min]
    rel.sort(key=lambda r: -float(r.get("nota_fit") or 0))
    return rel


def montar_mensagem(relevantes: list[dict]) -> str:
    linhas = []
    for r in relevantes[:MAX_NO_AVISO]:
        linhas.append(f"{float(r['nota_fit']):.1f} · {r['titulo']} — {r['empresa']}\n{r.get('link', '')}")
    if len(relevantes) > MAX_NO_AVISO:
        linhas.append(f"+ {len(relevantes) - MAX_NO_AVISO} no arquivo novas_*.csv")
    return "\n\n".join(linhas)


def enviar_ntfy(topico: str, titulo: str, mensagem: str, email: str | None = None,
                token: str | None = None) -> None:
    # JSON (e não headers) pra acento no título funcionar
    corpo = {"topic": topico, "title": titulo, "message": mensagem[:3900], "tags": ["briefcase"]}
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    if email:
        corpo["email"] = email  # o ntfy encaminha o aviso por e-mail
    resp = requests.post("https://ntfy.sh/", json=corpo, headers=headers, timeout=20)
    if resp.status_code == 400 and email:
        # e-mail recusado (sem conta/token, ou limite do plano): manda só pro app
        print(f"  [aviso] ntfy recusou o e-mail ({resp.text[:120].strip()}); aviso só no app.",
              file=sys.stderr)
        corpo.pop("email")
        resp = requests.post("https://ntfy.sh/", json=corpo, headers=headers, timeout=20)
    resp.raise_for_status()


def salvar_e_avisar(novas: list[dict], out_dir: Path, config: dict) -> list[dict]:
    relevantes = filtrar_relevantes(novas, config)
    path = out_dir / f"novas_{dt.date.today().isoformat()}.csv"
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=COLUNAS)
        w.writeheader()
        for r in relevantes:
            w.writerow({c: r.get(c, "") for c in COLUNAS})
    print(f"\n{len(novas)} vagas novas desde a última rodada; {len(relevantes)} relevantes "
          f"salvas em {path}")
    for r in relevantes[:MAX_NO_AVISO]:
        print(f"  {float(r['nota_fit']):4.1f}  {r['titulo']} — {r['empresa']}")

    topico = _ler_env("NTFY_TOPIC")
    if topico and relevantes:
        n = len(relevantes)
        titulo = f"Radar: {n} vaga{'s' if n > 1 else ''} nova{'s' if n > 1 else ''} pra você"
        try:
            email = _ler_env("NTFY_EMAIL")
            enviar_ntfy(topico, titulo, montar_mensagem(relevantes), email=email,
                        token=_ler_env("NTFY_TOKEN"))
            print("  aviso enviado" + (" por e-mail (ntfy)." if email else " pro celular (ntfy)."))
        except requests.RequestException as e:
            print(f"  [aviso] não consegui mandar o aviso pro celular: {e}", file=sys.stderr)
    return relevantes

"""
Coletor do Working Nomads (https://www.workingnomads.com), board de vagas
remotas com API pública, sem chave:

    GET https://www.workingnomads.com/api/exposed_jobs/
        -> [{"url", "title", "description" (HTML), "company_name",
             "category_name", "tags", "location", "pub_date"}]

Sem busca por termo: a API devolve as vagas recentes de uma vez; o screen.py
aplica o filtro de título e aqui fica só o que aceita Brasil/LATAM/anywhere.

Criado em 07/11/2026 para o Perfil 2. Formato segundo a documentação do site —
conferir na 1ª rodada real.
"""
from __future__ import annotations

import requests

from ._util import aceita_brasil, registrar_board, strip_html, title_matches

WORKINGNOMADS_URL = "https://www.workingnomads.com/api/exposed_jobs/"
UA = {"User-Agent": "Mozilla/5.0 (compatible; job-fit-screener/1.0)", "Accept": "application/json"}


def fetch_workingnomads(term_filter=None, limit: int | None = None) -> list[dict]:
    resp = requests.get(WORKINGNOMADS_URL, headers=UA, timeout=30)
    resp.raise_for_status()
    brutas = resp.json() or []
    registrar_board("workingnomads", "workingnomads", len(brutas))
    jobs = []
    for j in brutas:
        titulo = strip_html(j.get("title") or "")
        if not titulo or (term_filter is not None and not title_matches(titulo, term_filter)):
            continue
        local = (j.get("location") or "").strip()
        descricao = strip_html(j.get("description") or "")
        if not aceita_brasil(local, descricao):
            continue
        jobs.append({
            "id": f"workingnomads:{j.get('url')}",
            "fonte": "workingnomads",
            "titulo": titulo,
            "empresa": (j.get("company_name") or "").strip(),
            "cidade": f"Remoto internacional — {local or 'local não informado'}",
            "estado": "",
            "remoto": True,
            "link": j.get("url") or "",
            "descricao": descricao,
            "publicado_em": str(j.get("pub_date") or "")[:10],
        })
    return jobs[:limit] if limit is not None else jobs

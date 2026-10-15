"""
Coletor do Workable (https://jobs.workable.com) — o portal que reúne as vagas
de todas as empresas que usam o Workable como ATS (muitas startups e empresas
de tecnologia estrangeiras com vagas no Brasil).

Usa a API JSON da própria busca do portal, confirmada em 15/10/2026:

    GET https://jobs.workable.com/api/v1/jobs?query=<termo>&location=Brazil
        -> {"totalSize", "nextPageToken", "jobs": [...]}  (20 vagas por página;
           a próxima página vem com ?pageToken=<nextPageToken>)

Cada vaga já traz a descrição completa (description + requirementsSection +
benefitsSection, em HTML), empresa, local, modelo (workplace) e data.
O robots.txt do portal bloqueia as páginas HTML de busca (/search?...), não a
API; e declara "search=yes".

A busca casa o termo também na descrição ("growth" traz engenheiro etc.), por
isso o screen.py aplica o filtro de título (title_keywords) nestas vagas.
"""
from __future__ import annotations

import time

import requests

from ._util import strip_html, title_matches

WORKABLE_URL = "https://jobs.workable.com/api/v1/jobs"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; job-fit-screener/1.0)", "Accept": "application/json"}


def _to_job(j: dict) -> dict:
    loc = j.get("location") or {}
    empresa = (j.get("company") or {}).get("title", "")
    partes = [j.get("description"), j.get("requirementsSection"), j.get("benefitsSection")]
    return {
        "id": f"workable:{j.get('id')}",
        "fonte": "workable",
        "titulo": (j.get("title") or "").strip(),
        "empresa": empresa,
        "cidade": loc.get("city") or loc.get("countryName") or "",
        "estado": loc.get("subregion") or "",
        "remoto": j.get("workplace") == "remote",
        "link": j.get("url", ""),
        "descricao": strip_html(" ".join(p for p in partes if p)),
        "publicado_em": (j.get("created") or "")[:10],
    }


def fetch_workable(term: str, location: str = "Brazil", limit: int = 60, term_filter=None,
                   max_pages: int = 10) -> list[dict]:
    jobs: list[dict] = []
    token = ""
    for _ in range(max_pages):  # termo amplo + filtro de título não pagina sem fim
        if len(jobs) >= limit:
            break
        params = {"query": term, "location": location}
        if token:
            params["pageToken"] = token
        resp = requests.get(WORKABLE_URL, params=params, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        payload = resp.json()
        for j in payload.get("jobs") or []:
            if j.get("state", "published") != "published":
                continue
            if term_filter is not None and not title_matches(j.get("title", ""), term_filter):
                continue
            jobs.append(_to_job(j))
        token = payload.get("nextPageToken") or ""
        if not token or not payload.get("jobs"):
            break
        time.sleep(0.5)  # portal público — sem martelar
    return jobs[:limit]

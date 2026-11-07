"""
Coletor do Recruitee, ATS de muitas agências e empresas pequenas/médias
europeias. Cada empresa tem uma API pública, sem chave:

    GET https://<empresa>.recruitee.com/api/offers/
        -> {"offers": [{"id", "title", "description" (HTML), "requirements"
            (HTML), "location", "city", "country", "remote", "careers_url",
            "published_at", "company_name", ...}]}

Criado em 07/11/2026 (fontes-vagas.md, seção 5). Config:
sources.recruitee.empresas: [{empresa: "Nome", id: "<subdomínio>"}].
"""
from __future__ import annotations

import requests

from ._util import registrar_board, strip_html, title_matches

RECRUITEE_URL = "https://{id}.recruitee.com/api/offers/"
UA = {"User-Agent": "Mozilla/5.0 (compatible; job-fit-screener/1.0)", "Accept": "application/json"}


def fetch_recruitee(id_: str, empresa: str = "", term_filter=None, limit: int | None = None) -> list[dict]:
    resp = requests.get(RECRUITEE_URL.format(id=id_), headers=UA, timeout=30)
    resp.raise_for_status()
    ofertas = resp.json().get("offers") or []
    registrar_board("recruitee", empresa or id_, len(ofertas))
    jobs = []
    for o in ofertas:
        if limit is not None and len(jobs) >= limit:
            break
        titulo = (o.get("title") or "").strip()
        if not titulo or (term_filter is not None and not title_matches(titulo, term_filter)):
            continue
        local = o.get("location") or ", ".join(x for x in (o.get("city"), o.get("country")) if x)
        jobs.append({
            "id": f"recruitee:{id_}:{o.get('id')}",
            "fonte": "recruitee",
            "titulo": titulo,
            "empresa": empresa or o.get("company_name") or id_,
            "cidade": local or "",
            "estado": "",
            "remoto": bool(o.get("remote")),
            "link": o.get("careers_url") or o.get("url") or "",
            "descricao": strip_html(" ".join(p for p in (o.get("description"), o.get("requirements")) if p)),
            "publicado_em": str(o.get("published_at") or o.get("created_at") or "")[:10],
        })
    return jobs

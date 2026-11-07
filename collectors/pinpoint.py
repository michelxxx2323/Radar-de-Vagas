"""
Coletor do Pinpoint, ATS de empresas pequenas e médias (UK/EUA). Cada empresa
tem um JSON público, sem chave:

    GET https://<empresa>.pinpointhq.com/postings.json
        -> {"data": [{"id", "title", "description" (HTML), "url",
            "workplace_type" ("remote", "hybrid", "onsite"),
            "location": {"name", "city", "province"}, ...}]}

Criado em 07/11/2026 (fontes-vagas.md, seção 5). Config:
sources.pinpoint.empresas: [{empresa: "Nome", id: "<subdomínio>"}].
"""
from __future__ import annotations

import requests

from ._util import registrar_board, strip_html, title_matches

PINPOINT_URL = "https://{id}.pinpointhq.com/postings.json"
UA = {"User-Agent": "Mozilla/5.0 (compatible; job-fit-screener/1.0)", "Accept": "application/json"}


def fetch_pinpoint(id_: str, empresa: str = "", term_filter=None, limit: int | None = None) -> list[dict]:
    resp = requests.get(PINPOINT_URL.format(id=id_), headers=UA, timeout=30)
    resp.raise_for_status()
    vagas = resp.json().get("data") or []
    registrar_board("pinpoint", empresa or id_, len(vagas))
    jobs = []
    for v in vagas:
        if limit is not None and len(jobs) >= limit:
            break
        titulo = (v.get("title") or "").strip()
        if not titulo or (term_filter is not None and not title_matches(titulo, term_filter)):
            continue
        loc = v.get("location") or {}
        local = loc.get("name") or ", ".join(x for x in (loc.get("city"), loc.get("province")) if x)
        partes = [v.get(k) for k in ("description", "key_responsibilities", "skills_knowledge_expertise") if v.get(k)]
        jobs.append({
            "id": f"pinpoint:{id_}:{v.get('id')}",
            "fonte": "pinpoint",
            "titulo": titulo,
            "empresa": empresa or id_,
            "cidade": local or "",
            "estado": "",
            "remoto": (v.get("workplace_type") or "").lower() == "remote",
            "link": v.get("url") or "",
            "descricao": strip_html(" ".join(partes)),
            "publicado_em": "",
        })
    return jobs

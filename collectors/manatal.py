"""
Coletor do Manatal, o ATS da Purple Hiring (e de outras agências pequenas),
pela API pública da career page. Confirmado em 08/11/2026:

    GET https://api.manatal.com/open/v3/career-page/<conta>/jobs/
        -> {"count", "next", "previous", "results": [{"id", "hash",
            "position_name", "description" (HTML), "is_remote", "country",
            "state", "city", "location_display", "contract_details", ...}]}

Paginação no estilo DRF: segue `next` (que volta no host core.api.manatal.com)
até vir null. Não há data de publicação. Na Purple os campos de local vêm
vazios e o "LATAM Only" só aparece no título, então a elegibilidade olha o
título junto com a descrição.

Página pública da vaga: https://www.careers-page.com/<conta>/job/<hash>.
Config: sources.manatal.empresas: [{empresa: "Purple Hiring", id: "purple-careers"}].
"""
from __future__ import annotations

import requests

from ._util import aceita_brasil, registrar_board, strip_html, title_matches

API = "https://api.manatal.com/open/v3/career-page/{id}/jobs/"
PAGINA = "https://www.careers-page.com/{id}/job/{hash}"
UA = {"User-Agent": "Mozilla/5.0 (compatible; job-fit-screener/1.0)", "Accept": "application/json"}


def fetch_manatal(id_: str, empresa: str = "", term_filter=None, limit: int | None = None,
                  max_paginas: int = 20) -> list[dict]:
    url, vagas = API.format(id=id_), []
    for _ in range(max_paginas):
        resp = requests.get(url, headers=UA, timeout=30)
        resp.raise_for_status()
        dados = resp.json() or {}
        vagas.extend(dados.get("results") or [])
        url = dados.get("next")
        if not url:
            break
    nome = empresa or id_
    registrar_board("manatal", nome, len(vagas))
    jobs = []
    for v in vagas:
        if limit is not None and len(jobs) >= limit:
            break
        titulo = (v.get("position_name") or "").strip()
        if not titulo or (term_filter is not None and not title_matches(titulo, term_filter)):
            continue
        remoto = bool(v.get("is_remote"))
        local = v.get("location_display") or ", ".join(
            x for x in (v.get("city"), v.get("state"), v.get("country")) if x)
        descricao = strip_html(v.get("description") or "")
        if remoto and not aceita_brasil(local or "Remote", f"{titulo} {descricao}"):
            continue
        jobs.append({
            "id": f"manatal:{id_}:{v.get('id')}",
            "fonte": "manatal",
            "titulo": titulo,
            "empresa": nome,
            "cidade": f"Remoto — {local}" if remoto and local else (local or ("Remoto" if remoto else "")),
            "estado": "",
            "remoto": remoto,
            "link": PAGINA.format(id=id_, hash=v.get("hash")),
            "descricao": descricao,
            "publicado_em": "",
        })
    return jobs

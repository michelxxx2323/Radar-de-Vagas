"""
Coletor via API pública da Ashby (Job Board API) — sem autenticação.

Endpoint: https://api.ashbyhq.com/posting-api/job-board/<company>

Assim como Greenhouse/InHire/Lever, a Ashby não tem busca entre empresas —
cada empresa tem seu próprio board (ex.: jobs.ashbyhq.com/nubank). Este
coletor varre a lista configurada em config.yaml (sources.ashby.boards) e
devolve TODAS as vagas abertas de cada empresa, sem filtro de termo, deixando
o Jev decidir a aderência — mesmo padrão já usado para Greenhouse/InHire/Lever
neste projeto.

Schema confirmado em 21/09/2026 inspecionando a API real (exemplo:
api.ashbyhq.com/posting-api/job-board/nubank): resposta é
{"jobs": [...]}, cada job com id, title, location, isRemote, workplaceType,
publishedAt, jobUrl, descriptionPlain/descriptionHtml.

A Ashby é usada por bastante empresa de tech/scale-up global e por algumas
brasileiras (ex.: Nubank, Doctoralia/Docplanner, já vistos neste projeto).
"""
from __future__ import annotations

import requests

from ._util import registrar_board, strip_html

ASHBY_URL = "https://api.ashbyhq.com/posting-api/job-board/{company}"


def fetch_ashby(company: str) -> list[dict]:
    resp = requests.get(ASHBY_URL.format(company=company), timeout=20)
    if resp.status_code == 404:
        registrar_board("ashby", company, 0, "404")
        return []  # slug inválido, ou a empresa não usa Ashby (ou não é pública)
    resp.raise_for_status()
    payload = resp.json()
    postings = payload.get("jobs", []) if isinstance(payload, dict) else []
    registrar_board("ashby", company, len(postings))

    jobs = []
    for j in postings:
        titulo = j.get("title", "")
        cidade = j.get("location", "") or ""
        workplace_type = (j.get("workplaceType") or "").lower()
        remoto = bool(j.get("isRemote")) or workplace_type == "remote" or "remote" in cidade.lower() or "remoto" in cidade.lower()
        descricao = j.get("descriptionPlain") or j.get("descriptionHtml") or ""
        publicado_em = (j.get("publishedAt") or "")[:10]

        jobs.append(
            {
                "id": f"ashby:{company}:{j.get('id')}",
                "fonte": "ashby",
                "titulo": titulo,
                "empresa": company,
                "cidade": cidade,
                "estado": "",
                "remoto": remoto,
                "link": j.get("jobUrl", ""),
                "descricao": strip_html(descricao),
                "publicado_em": publicado_em,
            }
        )
    return jobs

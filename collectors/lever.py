"""
Coletor via API pública oficial da Lever (Postings API) — sem autenticação.

Endpoint: https://api.lever.co/v0/postings/<company>?mode=json

Assim como Greenhouse e InHire, a Lever não tem busca entre empresas — cada
empresa tem seu próprio board (ex.: jobs.lever.co/ciandt). Este coletor varre
a lista configurada em config.yaml (sources.lever.boards) e devolve TODAS as
vagas abertas de cada empresa, sem filtro de termo, deixando o Jev decidir a
aderência — mesmo padrão já usado para Greenhouse/InHire neste projeto.

Schema confirmado em 21/09/2026 inspecionando a API real (exemplo:
api.lever.co/v0/postings/ciandt?mode=json): cada posting tem id, text
(título), categories {location, team, department, commitment, allLocations},
country, workplaceType ("remote"/"on-site"/"hybrid"), descriptionPlain,
createdAt (epoch ms) e hostedUrl.
"""
from __future__ import annotations

import datetime as dt

import requests

from ._util import strip_html

LEVER_URL = "https://api.lever.co/v0/postings/{company}"


def fetch_lever(company: str, country: str = "") -> list[dict]:
    """`country` (ex. "BR") fica só com vagas daquele país (campo `country` da
    API) — pra boards globais como o da Lalamove, com poucas vagas no Brasil."""
    resp = requests.get(LEVER_URL.format(company=company), params={"mode": "json"}, timeout=20)
    if resp.status_code == 404:
        return []  # slug inválido, ou a empresa não usa Lever (ou não é pública)
    resp.raise_for_status()
    postings = resp.json()
    if not isinstance(postings, list):
        return []

    jobs = []
    for j in postings:
        if country and (j.get("country") or "").upper() != country.upper():
            continue
        titulo = j.get("text", "")
        categories = j.get("categories") or {}
        cidade = categories.get("location", "") or j.get("country", "") or ""
        workplace_type = (j.get("workplaceType") or "").lower()
        remoto = (
            workplace_type == "remote"
            or "remote" in cidade.lower()
            or "remoto" in cidade.lower()
        )
        descricao = strip_html(j.get("descriptionPlain") or j.get("description") or "")
        # responsabilidades/requisitos ficam em "lists" (cada uma com um título,
        # ex. "REQUIRED QUALIFICATIONS:", e um <li> por item), fora da descrição.
        for lst in j.get("lists") or []:
            itens = strip_html(lst.get("content", ""))
            if itens:
                descricao += f"\n\n{strip_html(lst.get('text', ''))} {itens}"

        jobs.append(
            {
                "id": f"lever:{company}:{j.get('id')}",
                "fonte": "lever",
                "titulo": titulo,
                "empresa": company,
                "cidade": cidade,
                "estado": "",
                "remoto": remoto,
                "link": j.get("hostedUrl", ""),
                "descricao": descricao,
                "publicado_em": _fmt_epoch_ms(j.get("createdAt")),
            }
        )
    return jobs


def _fmt_epoch_ms(ms) -> str:
    if not ms:
        return ""
    try:
        return dt.datetime.utcfromtimestamp(ms / 1000).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return ""

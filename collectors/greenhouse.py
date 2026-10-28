"""
Coletor via API pública oficial da Greenhouse (Job Board API) — sem
autenticação, documentada em https://docs.greenhouse.io/job-board.html.

A Greenhouse não tem uma busca única entre empresas: cada empresa tem seu
próprio "board" (ex.: https://boards.greenhouse.io/nubank). Por isso este
coletor não busca por termo livre — ele varre a lista de empresas
configurada em config.yaml (sources.greenhouse.boards) e devolve as vagas
abertas de cada uma, com um filtro opcional por termo no título.
"""
from __future__ import annotations

import requests

from ._util import registrar_board, strip_html, title_matches

GREENHOUSE_URL = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs"


def fetch_greenhouse(board: str, term_filter="") -> list[dict]:
    """term_filter: trecho ou lista de trechos do título (ver title_matches)."""
    url = GREENHOUSE_URL.format(board=board)
    resp = requests.get(url, params={"content": "true"}, timeout=20)
    if resp.status_code == 404:
        registrar_board("greenhouse", board, 0, "404")
        return []  # board token inválido, ou a empresa não usa Greenhouse (ou não é pública)
    resp.raise_for_status()
    payload = resp.json()
    registrar_board("greenhouse", board, len(payload.get("jobs") or []))

    jobs = []
    for j in payload.get("jobs", []):
        titulo = j.get("title", "")
        if not title_matches(titulo, term_filter):
            continue
        location = (j.get("location") or {}).get("name", "")
        jobs.append(
            {
                "id": f"greenhouse:{board}:{j.get('id')}",
                "fonte": "greenhouse",
                "titulo": titulo,
                "empresa": board,
                "cidade": location,
                "estado": "",
                "remoto": "remote" in location.lower() or "remoto" in location.lower(),
                "link": j.get("absolute_url", ""),
                "descricao": strip_html(j.get("content", "")),
                "publicado_em": j.get("updated_at", ""),
            }
        )
    return jobs

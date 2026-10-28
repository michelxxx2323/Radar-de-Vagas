"""
Coletor das vagas da Shopee Brasil (careers.shopee.com.br).

O site é um app Vue que busca as vagas numa API JSON sem autenticação —
descoberta lendo o bundle do site em 25/09/2026:

    GET https://careers.shopee.com.br/api/positions/search/?offset=<n>&limit=50
        -> {"positions": [{id, position_name, region, ...}], "meta": {record_count, ...}}
    GET https://careers.shopee.com.br/api/positions/detail/?id=<id>
        -> position_presentation[0].position_description = JSON (string) com
           job_description / job_requirement em HTML

A busca ignora parâmetros de termo/filtro (só offset/limit funcionam) e traz
vagas de TODOS os países da Shopee (~2.600) — o Brasil é a região 13 (~500
vagas, muitas operacionais/logística). Por isso o coletor pagina tudo, fica só
com a região do Brasil, aplica o filtro de título ANTES de buscar a descrição
(uma requisição por vaga) e só então busca os detalhes.
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor

import requests

from . import _cache
from ._util import strip_html, title_matches

SHOPEE_SEARCH_URL = "https://careers.shopee.com.br/api/positions/search/"
SHOPEE_DETAIL_URL = "https://careers.shopee.com.br/api/positions/detail/"
SHOPEE_JOB_URL = "https://careers.shopee.com.br/job-detail/{id}/"
BRASIL_REGION = 13
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36",
    "Accept": "application/json",
}
_PAGE = 50
_WORKERS = 6  # chamadas simultâneas — rápido sem martelar o site


def fetch_shopee(term_filter="", fetch_descriptions: bool = True, limit: int | None = None) -> list[dict]:
    """term_filter: trecho ou lista de trechos do título (ver title_matches)."""
    # cada chamada leva ~2s (medido em 25/09/2026) e são ~52 páginas + 1 detalhe
    # por vaga aprovada — em série passava de 5 min, por isso vai em paralelo
    first = _get_page(0)
    total = (first.get("meta") or {}).get("record_count", 0)
    positions: list[dict] = list(first.get("positions") or [])
    with ThreadPoolExecutor(_WORKERS) as ex:
        for payload in ex.map(_get_page, range(_PAGE, total, _PAGE)):
            positions += payload.get("positions") or []

    escolhidas = []
    for p in positions:
        titulo = (p.get("position_name") or "").strip()
        if p.get("region") == BRASIL_REGION and title_matches(titulo, term_filter):
            escolhidas.append((p, titulo))
    if limit is not None:
        escolhidas = escolhidas[:limit]

    if fetch_descriptions:
        with ThreadPoolExecutor(_WORKERS) as ex:
            descricoes = list(ex.map(
                lambda id_: _cache.descricao(f"shopee:{id_}", lambda: _fetch_description(id_)),
                [p["id"] for p, _ in escolhidas]))
    else:
        descricoes = [""] * len(escolhidas)

    jobs = []
    for (p, titulo), descricao in zip(escolhidas, descricoes):
        jobs.append(
            {
                "id": f"shopee:{p['id']}",
                "fonte": "shopee",
                "titulo": titulo,
                "empresa": "Shopee",
                "cidade": "",
                "estado": "",
                "remoto": "remot" in titulo.lower(),
                "link": SHOPEE_JOB_URL.format(id=p["id"]),
                "descricao": descricao,
                "publicado_em": "",
            }
        )
    return jobs


def _get_page(offset: int, tentativas: int = 3) -> dict:
    # são ~52 páginas por rodada; um timeout isolado (visto ao vivo em 25/09)
    # não pode derrubar a Shopee inteira
    for i in range(1, tentativas + 1):
        try:
            resp = requests.get(
                SHOPEE_SEARCH_URL, params={"offset": offset, "limit": _PAGE}, headers=_HEADERS, timeout=30
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException:
            if i == tentativas:
                raise
            time.sleep(2 * i)
    return {}


def _fetch_description(job_id) -> str:
    try:
        resp = requests.get(SHOPEE_DETAIL_URL, params={"id": job_id}, headers=_HEADERS, timeout=20)
        if resp.status_code != 200:
            return ""
        return _parse_description(resp.json())
    except (requests.RequestException, ValueError):
        return ""  # segue sem descrição — melhor uma vaga incompleta do que travar a coleta


def _parse_description(detail: dict) -> str:
    partes = []
    for pres in detail.get("position_presentation") or []:
        raw = pres.get("position_description") or ""
        try:
            d = json.loads(raw) if isinstance(raw, str) else raw
        except ValueError:
            d = {"job_description": raw}
        if not isinstance(d, dict):
            continue
        if d.get("job_description"):
            partes.append(strip_html(d["job_description"]))
        if d.get("job_requirement"):
            partes.append("Requisitos: " + strip_html(d["job_requirement"]))
    return "\n\n".join(partes)

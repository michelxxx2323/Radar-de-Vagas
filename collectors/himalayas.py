"""
Coletor do Himalayas (https://himalayas.app), board de vagas remotas com API
pública e gratuita, sem chave. Usa a busca:

    GET https://himalayas.app/jobs/api/search?q=<termo>&country=Brazil&page=<n>
        -> {"jobs": [...], ...}

`country=Brazil` traz só vagas que aceitam candidato no Brasil (confirmado em
07/11/2026: "revenue operations" + Brazil = 62 vagas). O feed geral
(/jobs/api, 116 mil vagas, só as mais novas) quase não rendia: 1 vaga em 500.

Cada vaga traz `locationRestrictions` (países aceitos; vazio = mundo todo),
salário, senioridade e a descrição em HTML; por garantia ainda passa por
aceita_brasil. Termos de uso: linkar a página da vaga no Himalayas.
"""
from __future__ import annotations

import datetime as dt
import time

import requests

from ._util import aceita_brasil, registrar_board, strip_html, title_matches

HIMALAYAS_URL = "https://himalayas.app/jobs/api/search"
UA = {"User-Agent": "Mozilla/5.0 (compatible; job-fit-screener/1.0)", "Accept": "application/json"}
POR_PAGINA = 20


def _local(j: dict) -> str:
    restr = j.get("locationRestrictions") or []
    nomes = [r.get("name", "") if isinstance(r, dict) else str(r) for r in restr]
    return ", ".join(n for n in nomes if n) or "Worldwide"


def _data(valor) -> str:
    if isinstance(valor, (int, float)) and valor > 0:
        return dt.datetime.fromtimestamp(valor, dt.timezone.utc).date().isoformat()
    return str(valor or "")[:10]


def _salario(j: dict) -> str:
    lo, hi, moeda = j.get("minSalary"), j.get("maxSalary"), j.get("currency") or "USD"
    if lo and hi:
        return f"{moeda} {lo:,} a {hi:,} /ano"
    return f"{moeda} {(lo or hi):,} /ano" if (lo or hi) else ""


def fetch_himalayas(term: str, country: str = "Brazil", term_filter=None, max_pages: int = 3,
                    limit: int | None = None) -> list[dict]:
    jobs: list[dict] = []
    total = 0
    for pagina in range(1, max_pages + 1):
        params = {"q": term, "page": pagina}
        if country:
            params["country"] = country
        resp = requests.get(HIMALAYAS_URL, params=params, headers=UA, timeout=30)
        resp.raise_for_status()
        lote = resp.json().get("jobs") or []
        total += len(lote)
        for j in lote:
            titulo = (j.get("title") or "").strip()
            if not titulo or (term_filter is not None and not title_matches(titulo, term_filter)):
                continue
            local = _local(j)
            descricao = strip_html(j.get("description") or j.get("excerpt") or "")
            if not aceita_brasil(local, descricao):
                continue
            jobs.append({
                "id": f"himalayas:{j.get('guid') or j.get('applicationLink')}",
                "fonte": "himalayas",
                "titulo": titulo,
                "empresa": (j.get("companyName") or "").strip(),
                "cidade": f"Remoto internacional — {local}",
                "estado": "",
                "remoto": True,
                "link": j.get("guid") or j.get("applicationLink") or "",
                "descricao": descricao,
                "publicado_em": _data(j.get("pubDate")),
                "salario": _salario(j),
            })
        if len(lote) < POR_PAGINA or (limit is not None and len(jobs) >= limit):
            break
        time.sleep(1)  # API gratuita — sem martelar
    registrar_board("himalayas", term, total)
    return jobs[:limit] if limit is not None else jobs

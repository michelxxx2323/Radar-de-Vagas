"""
Coletor da CloudTask (marketplace de profissionais LATAM para empresas dos
EUA: vendas, marketing, operações, GTM). Busca pública no estilo Algolia,
confirmada em 08/11/2026:

    POST https://app.cloudtask.com/api/search/job_posts
         {"requests": [{"indexName": "job_posts",
                        "params": {"query": "", "hitsPerPage": 100, "page": 0}}]}
        -> {"results": [{"nbHits", "page", "nbPages", "hits": [...]}]}

Cada hit: title_sem (o título de verdade; "role" é só a categoria),
role_category, job_to_be_dones (tarefas), software_skills, price (faixa
mensal em dólar), work_arrangements (["Remote"], ["On-site"]...), created_at,
page_url. Não há país: a CloudTask contrata na América Latina (a página da
vaga diz applicantLocationRequirements "Latin America"), então vaga remota
conta como LATAM, e presencial ou com cidade no título ("Medellín-Based") sai.
A descrição do hit é um texto genérico; o coletor monta a descrição com a
categoria, as tarefas e as ferramentas. Vagas antigas (há 9 de 2024-2025)
saem por `max_dias`.
"""
from __future__ import annotations

import datetime as dt
import re

import requests

from ._util import _fold, registrar_board, title_matches

URL = "https://app.cloudtask.com/api/search/job_posts"
BASE = "https://app.cloudtask.com"
UA = {"User-Agent": "Mozilla/5.0 (compatible; job-fit-screener/1.0)", "Content-Type": "application/json",
      "Accept": "application/json"}
_PRESENCIAL_NO_TITULO = re.compile(r"\b(on-?site|in-?office|hybrid|[a-z]+-based)\b|medell[ií]n|bogot[aá]", re.I)


def _recente(criado: str, max_dias: int | None, hoje: dt.date | None = None) -> bool:
    if not max_dias or not criado:
        return True
    try:
        data = dt.date.fromisoformat(criado[:10])
    except ValueError:
        return True
    return ((hoje or dt.date.today()) - data).days <= max_dias


def fetch_cloudtask(term_filter=None, limit: int | None = None, max_dias: int | None = 120,
                    max_paginas: int = 10, hoje: dt.date | None = None) -> list[dict]:
    hits, pagina = [], 0
    while pagina < max_paginas:
        corpo = {"requests": [{"indexName": "job_posts", "params": {"query": "", "hitsPerPage": 100, "page": pagina}}]}
        resp = requests.post(URL, json=corpo, headers=UA, timeout=30)
        resp.raise_for_status()
        r = ((resp.json() or {}).get("results") or [{}])[0]
        hits.extend(r.get("hits") or [])
        pagina += 1
        if pagina >= (r.get("nbPages") or 0):
            break
    registrar_board("cloudtask", "cloudtask", len(hits))
    jobs = []
    for h in hits:
        if limit is not None and len(jobs) >= limit:
            break
        titulo = (h.get("title_sem") or h.get("role") or "").strip()
        if not titulo or (term_filter is not None and not title_matches(titulo, term_filter)):
            continue
        arranjos = [_fold(a) for a in h.get("work_arrangements") or []]
        if not any("remote" in a for a in arranjos) or _PRESENCIAL_NO_TITULO.search(titulo):
            continue
        if not _recente(h.get("created_at") or "", max_dias, hoje):
            continue
        partes = [f"Categoria: {h['role_category']}." if h.get("role_category") else "",
                  f"Função: {h['role']}." if h.get("role") else "",
                  "Responsabilidades: " + "; ".join(h["job_to_be_dones"]) + "." if h.get("job_to_be_dones") else "",
                  "Ferramentas: " + ", ".join(h["software_skills"]) + "." if h.get("software_skills") else "",
                  "Disponibilidade: " + ", ".join(h["availability"]) + "." if h.get("availability") else "",
                  "Contratação remota na América Latina pela CloudTask (empresa dos EUA)."]
        jobs.append({
            "id": f"cloudtask:{h.get('id') or h.get('objectID')}",
            "fonte": "cloudtask",
            "titulo": titulo,
            "empresa": "CloudTask (cliente nos EUA)",
            "cidade": "Remoto — Latin America",
            "estado": "",
            "remoto": True,
            "link": BASE + (h.get("page_url") or ""),
            "descricao": " ".join(p for p in partes if p),
            "publicado_em": (h.get("created_at") or "")[:10],
            "salario": f"{h['price']} /mês" if h.get("price") else "",
        })
    return jobs

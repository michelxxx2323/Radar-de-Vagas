"""
Coletor do Breezy HR, ATS comum em agências pequenas (EUA). Cada empresa tem
um JSON público, sem chave:

    GET https://<empresa>.breezy.hr/json
        -> [{"id", "friendly_id", "name", "url", "published_date",
             "type": {"name"}, "location": {"name", "is_remote",
             "country": {"name"}}, "department", "salary", "company": {"name"}}]

O JSON não traz a descrição: com fetch_descriptions (padrão), a página de cada
vaga que passou no filtro de título é baixada uma vez (fica no cache de
descrições, collectors/_cache.py).

Criado em 07/11/2026 (fontes-vagas.md, seção 5). Config:
sources.breezy.empresas: [{empresa: "Nome", id: "<subdomínio>"}].
"""
from __future__ import annotations

import requests

from . import _cache
from ._util import registrar_board, strip_html, title_matches

BREEZY_URL = "https://{id}.breezy.hr/json"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128.0 Safari/537.36"}


def _descricao(url: str) -> str:
    try:
        resp = requests.get(url, headers=UA, timeout=30)
        resp.raise_for_status()
    except requests.RequestException:
        return ""
    html = resp.text
    # a descrição fica no bloco "description" da página; sem ele, o texto todo
    i = html.find('class="description"')
    return strip_html(html[i:] if i >= 0 else html)


def fetch_breezy(id_: str, empresa: str = "", term_filter=None, limit: int | None = None,
                 fetch_descriptions: bool = True) -> list[dict]:
    resp = requests.get(BREEZY_URL.format(id=id_), headers=UA, timeout=30)
    resp.raise_for_status()
    vagas = resp.json() or []
    registrar_board("breezy", empresa or id_, len(vagas))
    jobs = []
    for v in vagas:
        if limit is not None and len(jobs) >= limit:
            break
        titulo = (v.get("name") or "").strip()
        if not titulo or (term_filter is not None and not title_matches(titulo, term_filter)):
            continue
        loc = v.get("location") or {}
        pais = (loc.get("country") or {}).get("name", "") if isinstance(loc.get("country"), dict) else ""
        local = loc.get("name") or pais
        url = v.get("url") or ""
        descricao = _cache.descricao(f"breezy:{url}", lambda u=url: _descricao(u)) if (fetch_descriptions and url) else ""
        jobs.append({
            "id": f"breezy:{id_}:{v.get('id')}",
            "fonte": "breezy",
            "titulo": titulo,
            "empresa": empresa or (v.get("company") or {}).get("name") or id_,
            "cidade": local,
            "estado": "",
            "remoto": bool(loc.get("is_remote")),
            "link": url,
            "descricao": descricao,
            "publicado_em": str(v.get("published_date") or "")[:10],
            "salario": v.get("salary") or "",
        })
    return jobs

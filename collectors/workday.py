"""
Coletor de vagas no Workday (myworkdayjobs.com) — ATS de muita empresa grande
(PayPal, Natura, Rappi...). Cada empresa tem seu próprio site de vagas, e todo
site Workday expõe a mesma API JSON pública, sem login, que o próprio site usa
(confirmado em 29/09/2026 com PayPal, Natura e Rappi):

    POST https://<host>/wday/cxs/<tenant>/<site>/jobs
         {"appliedFacets": {...}, "limit": 20, "offset": 0, "searchText": ""}
      -> {"total": N, "jobPostings": [{title, externalPath, locationsText, postedOn}],
          "facets": [...]}
    GET  https://<host>/wday/cxs/<tenant>/<site><externalPath>
      -> {"jobPostingInfo": {title, jobDescription (HTML), location, externalUrl, ...}}

onde a URL pública do site é https://<host>/[<idioma>/]<site>, ex.
https://paypal.wd1.myworkdayjobs.com/jobs (tenant "paypal", site "jobs").

As vagas são globais; o filtro por país vem dos "facets" da própria resposta
(aninhados em locationMainGroup -> locationCountry / locations). O coletor
procura os valores cujo nome contém Brasil/Brazil (ou começa com "BRA") e
aplica só esses. A API devolve no máximo 20 por página.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import requests

from . import _cache
from ._util import registrar_board, strip_html, title_matches

_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36",
}
_PAGE = 20
_WORKERS = 6
_BRASIL_RE = re.compile(r"\b(brasil|brazil)\b|^bra\b", re.IGNORECASE)
_LOCALE_RE = re.compile(r"^[a-z]{2}-[A-Z]{2}$")


def parse_site(url: str) -> tuple[str, str, str]:
    """https://paypal.wd1.myworkdayjobs.com/en-US/jobs -> (host, tenant, site)"""
    u = urlparse(url)
    partes = [p for p in u.path.split("/") if p and not _LOCALE_RE.match(p)]
    if not partes:
        raise ValueError(f"URL do Workday sem o nome do site: {url}")
    return u.netloc, u.netloc.split(".")[0], partes[0]


def fetch_workday(url: str, empresa: str, term_filter="", limit: int | None = None,
                  fetch_descriptions: bool = True) -> list[dict]:
    host, tenant, site = parse_site(url)
    api = f"https://{host}/wday/cxs/{tenant}/{site}"

    primeira = _post(api, {}, 0)
    facets = _facets_brasil(primeira.get("facets") or [])
    if not facets:
        registrar_board("workday", empresa, 0, "0 vagas no Brasil")
        return []  # nenhuma localização no Brasil entre as vagas abertas

    postings: list[dict] = []
    offset = 0
    while True:
        page = _post(api, facets, offset)
        batch = page.get("jobPostings") or []
        postings += batch
        offset += _PAGE
        if not batch or offset >= (page.get("total") or 0):
            break

    registrar_board("workday", empresa, len(postings))
    escolhidas = [p for p in postings if title_matches(p.get("title") or "", term_filter)]
    if limit is not None:
        escolhidas = escolhidas[:limit]

    if fetch_descriptions:
        with ThreadPoolExecutor(_WORKERS) as ex:
            detalhes = list(ex.map(lambda p: _detail_em_cache(tenant, api, p.get("externalPath", "")), escolhidas))
    else:
        detalhes = [{}] * len(escolhidas)

    jobs = []
    for p, info in zip(escolhidas, detalhes):
        path = p.get("externalPath", "")
        titulo = (info.get("title") or p.get("title") or "").strip()
        local = info.get("location") or p.get("locationsText") or ""
        remoto = "remote" in (info.get("remoteType") or "").lower() or "remot" in local.lower()
        jobs.append(
            {
                "id": f"workday:{tenant}:{path.rsplit('_', 1)[-1] if path else titulo}",
                "fonte": "workday",
                "titulo": titulo,
                "empresa": empresa,
                "cidade": local,
                "estado": "",
                "remoto": remoto,
                "link": info.get("externalUrl") or f"https://{host}/{site}{path}",
                "descricao": strip_html(info.get("jobDescription") or ""),
                "publicado_em": info.get("startDate") or p.get("postedOn") or "",
            }
        )
    return jobs


def _post(api: str, facets: dict, offset: int) -> dict:
    body = {"appliedFacets": facets, "limit": _PAGE, "offset": offset, "searchText": ""}
    resp = requests.post(f"{api}/jobs", json=body, headers=_HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _facets_brasil(facets: list) -> dict:
    """Varre os facets (inclusive os aninhados) atrás de valores do Brasil.
    Prefere o filtro por país (locationCountry); se o site não tiver, usa a
    lista de localizações (locations) brasileiras."""
    achados: dict[str, list[str]] = {}

    def walk(items, param=None):
        for f in items or []:
            if not isinstance(f, dict):
                continue
            if "facetParameter" in f and isinstance(f.get("values"), list):
                walk(f["values"], f["facetParameter"])
            elif param and f.get("id") and _BRASIL_RE.search(f.get("descriptor") or ""):
                achados.setdefault(param, []).append(f["id"])

    walk(facets)
    for preferido in ("locationCountry", "Location_Country", "locations"):
        if preferido in achados:
            return {preferido: achados[preferido]}
    return dict(list(achados.items())[:1])


def _detail_em_cache(tenant: str, api: str, path: str) -> dict:
    chave = f"workday:{tenant}:{path}"
    info = _cache.obter(chave)
    if info is None:
        info = _detail(api, path)
        # só os campos usados abaixo; {} = falhou, não guarda
        info = {k: info[k] for k in ("title", "location", "remoteType", "externalUrl", "jobDescription",
                                     "startDate") if info.get(k)}
        if info:
            _cache.guardar(chave, info)
    return info


def _detail(api: str, path: str) -> dict:
    if not path:
        return {}
    try:
        resp = requests.get(f"{api}{path}", headers=_HEADERS, timeout=30)
        if resp.status_code != 200:
            return {}
        return resp.json().get("jobPostingInfo") or {}
    except (requests.RequestException, ValueError):
        return {}  # segue sem descrição — melhor uma vaga incompleta do que travar a coleta

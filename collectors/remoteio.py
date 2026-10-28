"""
Coletor do Remote.io (https://remote.io) — sem API pública de busca (o
robots.txt bloqueia /api/, só libera a de uma vaga por vez).

Até 27/10/2026 lia as páginas de listagem por local x área. Em 28/10/2026 a
listagem virou um app em JavaScript (o HTML vem vazio, 0 vagas). O que segue
no HTML:
- o sitemap de vagas ativas (sitemap-active-jobs.xml, ~4.700 vagas), com a
  área e o título no próprio link:
  /remote-jobs/operations/network-operations-manager-at-maven-clinic-2225888
- a página de cada vaga, com um JSON-LD JobPosting (descrição, data, salário
  e quem pode se candidatar: applicantLocationRequirements).

Então: filtra área e título pelo link, sem baixar nada; abre a página só das
que passam; fica com as que aceitam Brasil/LATAM. Cada página aberta (aceita
ou não) vai pro cache (collectors/_cache.py) e não é aberta de novo enquanto
a vaga estiver no ar. Como ~1.900 vagas passam pelo título, a 1ª rodada abre
só `max_novas` (as mais recentes) e o resto entra nas rodadas seguintes.
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import re

import requests

from . import _cache
from ._util import aceita_brasil, strip_html, title_matches

BASE = "https://remote.io"
SITEMAP_ATIVAS = BASE + "/sitemap-active-jobs.xml"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128.0 Safari/537.36"}
DEFAULT_AREAS = ["operations", "sales-partnerships", "business-development", "marketing",
                 "product", "finance", "data", "executive-c-suite", "other"]
MAX_NOVAS = 300
# a vaga fica no ar ~30 dias (validThrough); o local não muda nesse tempo
_TTL_DIAS = 30
_LOC_RE = re.compile(r"<loc>\s*(.*?)\s*</loc>")
_VAGA_RE = re.compile(r"^https://(?:www\.)?remote\.io/remote-jobs/([^/]+)/([^/]+)-(\d+)/?$")
# o bloco vem com outros atributos (data-job-posting="true")
_LDJSON_RE = re.compile(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S)


def _get(url: str) -> str:
    resp = requests.get(url, headers=UA, timeout=30)
    resp.raise_for_status()
    return resp.content.decode("utf-8", "replace")


def _titulo_do_link(slug: str) -> str:
    # "network-operations-manager-at-maven-clinic" -> "network operations manager"
    return slug.rsplit("-at-", 1)[0].replace("-", " ")


def _salario(base: dict) -> str:
    v = (base or {}).get("value") or {}
    lo, hi = v.get("minValue"), v.get("maxValue")
    moeda = base.get("currency", "") if base else ""
    if lo and hi:
        return f"{moeda} {lo:,} a {hi:,}".strip()
    return ""


def _nomes(req) -> str:
    """applicantLocationRequirements: um objeto ou uma lista ({"name": "Brazil"})."""
    itens = req if isinstance(req, list) else [req] if req else []
    return ", ".join(i.get("name", "") for i in itens if isinstance(i, dict) and i.get("name"))


def _ler_vaga(url: str, term_filter) -> dict | None:
    """O que interessa da página da vaga, no formato do cache: {"aceita": False}
    (só isso, pra não guardar descrição de vaga recusada) ou {"aceita": True,
    "vaga": {...}}. None se a página não abriu (tenta de novo na próxima)."""
    try:
        blocos = _LDJSON_RE.findall(_get(url))
    except requests.RequestException:
        return None
    posting: dict = {}
    for bloco in blocos:
        try:
            d = json.loads(bloco)
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("@type") == "JobPosting":
            posting = d
            break
    titulo = (posting.get("title") or "").strip()
    if not titulo or (term_filter is not None and not title_matches(titulo, term_filter)):
        return {"aceita": False}

    descricao = strip_html(posting.get("description", ""))
    # jobLocation: um lugar, ou uma lista quando a vaga tem vários escritórios
    lugares = posting.get("jobLocation")
    lugares = lugares if isinstance(lugares, list) else [lugares] if lugares else []
    enderecos = [(p.get("address") or {}) for p in lugares if isinstance(p, dict)]
    remoto = posting.get("jobLocationType") == "TELECOMMUTE"
    if remoto:
        local = _nomes(posting.get("applicantLocationRequirements"))
        aceita = aceita_brasil(local, descricao, anywhere_confiavel=False)
        cidade = f"Remoto internacional — {local}" if local else "Remoto internacional"
    else:
        # presencial/híbrido: só no Brasil
        no_brasil = [e for e in enderecos if isinstance(e, dict)
                     and str(e.get("addressCountry") or "").upper() in ("BR", "BRA", "BRAZIL", "BRASIL")]
        aceita = bool(no_brasil)
        cidade = no_brasil[0].get("addressLocality", "") if no_brasil else ""
    if not aceita:
        return {"aceita": False}
    salario = _salario(posting["baseSalary"]) if isinstance(posting.get("baseSalary"), dict) else ""
    return {"aceita": True, "vaga": {
        "titulo": titulo,
        "empresa": org.get("name", "") if isinstance(org := posting.get("hiringOrganization"), dict) else "",
        "cidade": cidade,
        "remoto": remoto,
        "descricao": descricao,
        "publicado_em": (posting.get("datePosted") or "")[:10],
        "salario": salario,
    }}


def fetch_remoteio(
    areas: list[str] | None = None,
    term_filter=None,
    limit: int | None = None,
    max_novas: int = MAX_NOVAS,
) -> list[dict]:
    areas = DEFAULT_AREAS if areas is None else areas
    candidatas: list[tuple[int, str]] = []
    for url in _LOC_RE.findall(_get(SITEMAP_ATIVAS)):
        m = _VAGA_RE.match(url)
        if not m:
            continue
        area, slug, id_ = m.groups()
        if areas and area not in areas:
            continue
        if term_filter is not None and not title_matches(_titulo_do_link(slug), term_filter):
            continue
        candidatas.append((int(id_), url))
    candidatas.sort(reverse=True)  # id maior = vaga mais recente
    if limit is not None:
        candidatas = candidatas[:limit]

    lidas = {id_: _cache.obter(f"remoteio:{id_}", ttl_dias=_TTL_DIAS) for id_, _ in candidatas}
    abrir = [(id_, url) for id_, url in candidatas if lidas[id_] is None][:max_novas]
    with cf.ThreadPoolExecutor(4) as ex:
        for (id_, _), info in zip(abrir, ex.map(lambda c: _ler_vaga(c[1], term_filter), abrir)):
            if info is not None:
                _cache.guardar(f"remoteio:{id_}", info)
                lidas[id_] = info

    jobs = []
    for id_, url in candidatas:
        info = lidas.get(id_)
        if info and info.get("aceita"):
            jobs.append({"id": f"remoteio:{id_}", "fonte": "remoteio", "estado": "", "link": url, **info["vaga"]})
    return jobs

"""
Coletor da Puente Talent (https://puentetalent.com/jobs), agência que coloca
profissionais da América Latina em empresas dos EUA, com pagamento mensal em
dólar. O site (Next.js) lê as vagas de uma API JSON pública, sem chave, e o
robots.txt libera tudo menos /apply/continue. Confirmado em 23/11/2026:

    GET https://puentetalent.com/api/jobs?page=<n>
        -> {"jobs": [...], "totalCount", "page", "pageSize": 6, "hasMore",
            "availableIndustries", "availableCountries", "isConfigured"}

A página é cumulativa: page=2 traz as 12 primeiras, page=11 traz as 62. Por
isso o coletor pede a 1ª página (para saber o total) e depois a última, que já
vem com tudo: 2 requisições por rodada. Se um dia a API passar a paginar de
verdade (a última página sem as anteriores), o coletor percebe pela contagem e
pede as páginas que faltam.

Cada vaga já vem com a descrição inteira (descriptionText), então não precisa
abrir página nenhuma. Campos usados: displayId (o número do link
/jobs/<título-em-slug>-<displayId>), title, descriptionText, category,
employmentType, locationType ("Remote" ou vazio), applicantCountries (lista de
países aceitos; a região "Latin America" vem expandida, com o Brasil),
otherCountry (o rótulo: "Latin America"), country (um país só, quando a vaga
é de lá: "Argentina", "United States"), salaryMin/salaryMax/salaryCurrency
(por mês), hiringOrganizationName ("confidential" quase sempre),
isActive, isTalentPool, createdOn.

A vaga fica se:
  1. está ativa e não é banco de talentos;
  2. o título passa no filtro do perfil;
  3. não é híbrida nem presencial;
  4. com lista de países, o Brasil está nela; sem lista, `country` não aponta
     outro país (sem lista e sem país, vale o padrão do site: só contrata na
     América Latina).
"""
from __future__ import annotations

import datetime as dt
import math
import re
import unicodedata

import requests

from ._util import _fold, registrar_board, strip_html, title_matches

BASE = "https://puentetalent.com"
API_URL = f"{BASE}/api/jobs"
UA = {"User-Agent": "Mozilla/5.0 (compatible; job-fit-screener/1.0)", "Accept": "application/json"}
MAX_PAGINAS = 50
_BRASIL = {"brazil", "brasil", "br", "bra"}
_PRESENCIAL = re.compile(r"\b(hybrid|hibrid[ao]|on-?site|in-?office|presencial)\b", re.I)
_SEM_PAIS = {"", "other", "remote", "latin america", "latam"}


def slug(titulo: str) -> str:
    """O slug do link do site: "Founder's Associate" -> "founder-s-associate"."""
    t = unicodedata.normalize("NFKD", titulo or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")


def link_da_vaga(v: dict) -> str:
    return f"{BASE}/jobs/{slug(v.get('title') or '')}-{v.get('displayId') or v.get('id')}"


def aceita_brasil_puente(v: dict) -> bool:
    paises = [_fold(str(p)).strip() for p in v.get("applicantCountries") or [] if p]
    if paises:
        return any(p in _BRASIL for p in paises)
    pais = _fold(str(v.get("country") or "")).strip()
    return pais in _SEM_PAIS or pais in _BRASIL


def _salario(v: dict) -> str:
    lo, hi = v.get("salaryMin"), v.get("salaryMax")
    if not lo and not hi:
        return (v.get("salaryLabel") or "").strip()
    moeda = (v.get("salaryCurrency") or "USD").strip()
    unidade = {"MONTH": "/mês", "YEAR": "/ano", "HOUR": "/hora"}.get((v.get("salaryUnitText") or "MONTH").upper(), "/mês")
    lo, hi = int(lo or hi), int(hi or lo)
    return f"{moeda} {lo:,} a {hi:,} {unidade}" if lo != hi else f"{moeda} {lo:,} {unidade}"


def _local(v: dict) -> str:
    paises = [p for p in v.get("applicantCountries") or [] if p]
    rotulo = (v.get("otherCountry") or "").strip()
    if rotulo:
        return rotulo
    if paises:
        return ", ".join(paises)
    return "Latin America"


def _empresa(v: dict) -> str:
    nome = (v.get("hiringOrganizationName") or "").strip()
    if not nome or nome.lower() == "confidential":
        return "Puente (cliente nos EUA)"
    return nome


def _recente(criado: str, max_dias: int | None, hoje: dt.date | None = None) -> bool:
    if not max_dias or not criado:
        return True
    try:
        data = dt.date.fromisoformat(criado[:10])
    except ValueError:
        return True
    return ((hoje or dt.date.today()) - data).days <= max_dias


def _pagina(n: int) -> dict:
    resp = requests.get(API_URL, params={"page": n}, headers=UA, timeout=30)
    resp.raise_for_status()
    return resp.json() or {}


def baixar_todas() -> list[dict]:
    """Todas as vagas da API, sem repetir. 2 requisições no caso normal."""
    primeira = _pagina(1)
    vagas = {v.get("id"): v for v in primeira.get("jobs") or []}
    total = int(primeira.get("totalCount") or 0)
    por_pagina = int(primeira.get("pageSize") or 0) or len(vagas) or 1
    if primeira.get("hasMore") and total > len(vagas):
        ultima = min(math.ceil(total / por_pagina), MAX_PAGINAS)
        for v in _pagina(ultima).get("jobs") or []:
            vagas.setdefault(v.get("id"), v)
        # paginação de verdade (a última página não trouxe as anteriores)
        n = 2
        while len(vagas) < total and n < ultima:
            for v in _pagina(n).get("jobs") or []:
                vagas.setdefault(v.get("id"), v)
            n += 1
    return list(vagas.values())


def fetch_puente(term_filter=None, limit: int | None = None, max_dias: int | None = None,
                 hoje: dt.date | None = None) -> list[dict]:
    todas = baixar_todas()
    registrar_board("puente", "puente", len(todas))
    jobs: list[dict] = []
    for v in todas:
        if limit is not None and len(jobs) >= limit:
            break
        titulo = (v.get("title") or "").strip()
        if not titulo or v.get("isActive") is False or v.get("isTalentPool"):
            continue
        if term_filter is not None and not title_matches(titulo, term_filter):
            continue
        modelo = (v.get("locationType") or "").strip()
        if _PRESENCIAL.search(f"{modelo} {v.get('locationLabel') or ''} {titulo}"):
            continue  # vaga híbrida/presencial não é remota (regra do Michel, 11/11/2026)
        if not aceita_brasil_puente(v):
            continue  # ex.: "Argentina · Remote", "Remote, OR, Oregon, United States"
        criado = str(v.get("createdOn") or "")[:10]
        if not _recente(criado, max_dias, hoje):
            continue
        local = _local(v)
        salario = _salario(v)
        cabecalho = [f"Área: {v['category']}." if v.get("category") else "",
                     f"Contrato: {v['employmentType']}." if v.get("employmentType") else "",
                     "Modelo: Remote.",
                     f"Aceita candidatos de: {', '.join(v['applicantCountries'])}." if v.get("applicantCountries")
                     else f"Aceita candidatos de: {local}.",
                     f"Salário: {salario}." if salario else "",
                     "Vaga da Puente Talent (profissionais da América Latina em empresas dos EUA, pagamento em dólar)."]
        descricao = (v.get("descriptionText") or "").strip() or strip_html(v.get("descriptionHtml") or "")
        jobs.append({
            "id": f"puente:{v.get('displayId') or v.get('id')}",
            "fonte": "puente",
            "titulo": titulo,
            "empresa": _empresa(v),
            "cidade": f"Remoto — {local}",
            "estado": "",
            "remoto": True,
            "link": link_da_vaga(v),
            "descricao": " ".join(p for p in cabecalho if p) + (" " + descricao if descricao else ""),
            "publicado_em": criado,
            "salario": salario,
        })
    return jobs

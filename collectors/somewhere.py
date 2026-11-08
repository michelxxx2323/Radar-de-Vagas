"""
Coletor da Somewhere (ex-Support Shepherd): staffing remoto que coloca gente de
LATAM, África do Sul e Filipinas em empresas dos EUA, muitas marcas DTC e de
e-commerce. Sem ATS: somewhere.com/jobs (Webflow) é um <iframe> de um app
Next.js + tRPC. Confirmado em 08/11/2026 (robots do somewhere.com libera tudo;
o host do iframe não tem robots.txt):

    GET https://salarycalculator.somewheretypingtest.com/api/trpc/job.list
        ?batch=1&input={"0":{"json":{"query":"","industries":[],
          "isSourcingUnit":null,"countries":["LATAM"],"otherCountry":null,
          "direction":"forward","limit":100,"cursor":<página>},
          "meta":{"values":{"otherCountry":["undefined"]},"v":1}}}
        -> [{"result":{"data":{"json":{"jobs":[...], "totalCount",
            "nextCursor", "hasNextPage"}}}}]

    cursor é o número da página (1, 2, 3...); limit vai até 100 (mais que isso
    dá 400). Em 08/11/2026: 1526 vagas, 807 com countries=["LATAM"].

Campos da vaga: id, name (título, muitas vezes com " - 67559832101" no fim),
slug, country ("LATAM,South Africa,Philippines"), job_type, job_industry,
isSourcingUnit (true = banco de talentos "SU - ...", vaga permanente) e
job_description_text (HTML completo). Não há data: os 13 primeiros dígitos do
slug são o momento da criação em ms (1793889433656 = 05/11/2026), e há vagas
de 2024 ainda no ar, então as mais velhas que `max_dias` saem.

Página pública da vaga: https://somewhere.com/jobs/apply?slug=<slug>.
"""
from __future__ import annotations

import datetime as dt
import html as _html
import json
import re
import time

import requests

from ._util import aceita_brasil, registrar_board, strip_html, title_matches

API = "https://salarycalculator.somewheretypingtest.com/api/trpc/job.list"
PAGINA = "https://somewhere.com/jobs/apply?slug={slug}"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128.0 Safari/537.36",
      "Accept": "application/json"}
# lixo nos títulos (rodada real de 08/11/2026): "Account Manager - [CG] 67559832101",
# "64792899631 -Accounts Payable Specialist", "Tax Coordinator - 21992",
# "C.G - eCommerce Content Coordinator", "Amazon PPC Manager (1) - 20340", "Analyst - "
_CODIGO_INICIO = re.compile(r"^\s*\d{8,}\s*[-–|]?\s*")
_CODIGO_FIM = re.compile(r"\s*[-–|]?\s*(\[\s*C\.?G\.?\s*\]\s*)?\d{5,}\s*$", re.I)
_TAG_INICIO = re.compile(r"^\s*(\[\s*C\.?G\.?\s*\]|C\.G\.?\s*[-–:])\s*", re.I)
_TAG_FIM = re.compile(r"\s*(\[\s*C\.?G\.?\s*\]|\(\d\))\s*$", re.I)
_PREFIXO_SU = re.compile(r"^\s*SU\s*[-–:]\s*", re.I)
# a descrição às vezes restringe o país mesmo com LATAM no campo country:
# "Search Location: South Africa, Eastern Europe, US, UK and Canada",
# "Fully remote, Philippines-based"
# "Search Location:", "Work Location :", "Location of Search :", "Priority Location:"...
_LOCAL_DESC = re.compile(r"\b(?:[a-z]+ )?location(?: of search)?\s*:\s*([^\n]{2,120})", re.I)
# só conta como restrição um local que nomeia país/região ("Location: Remote",
# "Location: Fully remote" ou "Our most recommended focus country..." não restringem)
_PAIS_CITADO = re.compile(r"\b(south africa|philippines|india|pakistan|egypt|kenya|nigeria|eastern europe|europe|"
                          r"united kingdom|united states|canada|mexico|colombia|argentina|peru|chile|costa rica|"
                          r"cardiff|london)\b", re.I)
_SIGLA_CITADA = re.compile(r"\b(US|USA|U\.S\.|UK|EU|EMEA|APAC)\b")
_FUSO = re.compile(r"\b(with|during|in|on|aligned with|overlap with|overlapping)\b[^,;()]*?\b(business hours|hours|time ?zones?|time)\b", re.I)
# (sem US/UK/Canada: "our US-based client" é o normal e não restringe o candidato)
_BASEADO = re.compile(r"\b(philippines|philippine|south africa|india|pakistan|eastern europe|"
                      r"colombia|mexico|argentina)[- ]based\b", re.I)
_CITA_BR = re.compile(r"\b(brazil|brasil|latam|latin america|south america)\b", re.I)


def _entrada(cursor: int, paises: list[str], limite: int) -> str:
    return json.dumps({"0": {"json": {"query": "", "industries": [], "isSourcingUnit": None,
                                      "countries": paises, "otherCountry": None, "direction": "forward",
                                      "limit": limite, "cursor": cursor},
                             "meta": {"values": {"otherCountry": ["undefined"]}, "v": 1}}},
                      separators=(",", ":"))


def ler_resposta(dados) -> dict:
    """O {"jobs", "nextCursor", "hasNextPage", ...} da resposta em lote do tRPC."""
    if isinstance(dados, list):
        dados = dados[0] if dados else {}
    return (((dados or {}).get("result") or {}).get("data") or {}).get("json") or {}


def criada_em(slug: str) -> dt.date | None:
    """A data de criação que vem nos 13 primeiros dígitos do slug (ms)."""
    m = re.match(r"(\d{13})", slug or "")
    if not m:
        return None
    try:
        return dt.datetime.fromtimestamp(int(m.group(1)) / 1000, tz=dt.timezone.utc).date()
    except (OverflowError, OSError, ValueError):
        return None


def limpar_titulo(nome: str) -> str:
    t = nome or ""
    for _ in range(3):  # "Analyst (1) - 20340", "[CG] 6755...": uma passada tira um pedaço
        antes = t
        t = _CODIGO_INICIO.sub("", t)
        t = _CODIGO_FIM.sub("", t)
        t = _TAG_INICIO.sub("", t)
        t = _TAG_FIM.sub("", t)
        t = re.sub(r"\s*[-–|:]\s*$", "", t).strip()
        if t == antes:
            break
    return t


def _texto_linhas(html: str) -> str:
    """O texto da descrição com uma linha por bloco (para achar "Location: ...")."""
    texto = _html.unescape(re.sub(r"(?i)<br\s*/?>|</(p|li|div|h\d)>", "\n", html or ""))
    texto = re.sub(r"<[^>]+>", " ", texto)
    return "\n".join(re.sub(r"\s+", " ", linha).strip() for linha in texto.splitlines())


def restringe_fora(html: str) -> bool:
    """True se a descrição limita a contratação a países sem Brasil/LATAM: algum
    "Location: ..." nomeia país e nenhum aceita Brasil/LATAM, ou a vaga é
    "Philippines-based" sem citar LATAM em lugar nenhum."""
    texto = _texto_linhas(html)
    valores = [m.group(1) for m in _LOCAL_DESC.finditer(texto)]
    if any(_CITA_BR.search(v) or aceita_brasil(v) for v in valores):
        return False
    # "Remote with US business hours": o país do fuso não restringe o candidato
    valores = [_FUSO.sub(" ", v) for v in valores]
    if any(_PAIS_CITADO.search(v) or _SIGLA_CITADA.search(v) for v in valores):
        return True
    return bool(_BASEADO.search(texto)) and not _CITA_BR.search(texto)


def fetch_somewhere(term_filter=None, limit: int | None = None, paises: list[str] | None = None,
                    max_dias: int | None = 120, incluir_banco_talentos: bool = False,
                    max_paginas: int = 12, pausa: float = 1.0, hoje: dt.date | None = None) -> list[dict]:
    paises = paises or ["LATAM"]
    vagas, cursor = [], 1
    for n in range(max_paginas):
        if n and pausa:
            time.sleep(pausa)
        resp = requests.get(API, params={"batch": "1", "input": _entrada(cursor, paises, 100)},
                            headers=UA, timeout=30)
        resp.raise_for_status()
        dados = ler_resposta(resp.json())
        vagas.extend(dados.get("jobs") or [])
        if not dados.get("hasNextPage") or not dados.get("nextCursor"):
            break
        cursor = dados["nextCursor"]
    registrar_board("somewhere", "Somewhere", len(vagas))
    hoje = hoje or dt.date.today()
    jobs, vistos = [], set()
    for v in vagas:
        if limit is not None and len(jobs) >= limit:
            break
        slug = (v.get("slug") or "").strip()
        if not slug or slug in vistos:
            continue
        vistos.add(slug)
        if v.get("isSourcingUnit") and not incluir_banco_talentos:
            continue  # banco de talentos permanente, não é vaga aberta
        titulo = _PREFIXO_SU.sub("", limpar_titulo(v.get("name") or ""))
        if not titulo or (term_filter is not None and not title_matches(titulo, term_filter)):
            continue
        criada = criada_em(slug)
        if max_dias and criada and (hoje - criada).days > max_dias:
            continue
        local = (v.get("country") or "").strip()
        aceitos = [p.strip() for p in local.split(",") if p.strip() and aceita_brasil(p.strip())]
        descricao = strip_html(v.get("job_description_text") or "")
        if not aceitos and not aceita_brasil(local, descricao):
            continue
        if restringe_fora(v.get("job_description_text") or ""):
            continue
        cabecalho = [f"Área: {v['job_industry']}." if v.get("job_industry") else "",
                     f"Contrato: {v['job_type']}." if v.get("job_type") else "",
                     f"Contrata em: {local}." if local else "",
                     "Vaga remota pela Somewhere (agência de staffing; cliente nos EUA)."]
        jobs.append({
            "id": f"somewhere:{v.get('id') or slug}",
            "fonte": "somewhere",
            "titulo": titulo,
            "empresa": "Somewhere",
            "cidade": "Remoto — " + ", ".join(aceitos) if aceitos else "Remoto",
            "estado": "",
            "remoto": True,
            "link": PAGINA.format(slug=slug),
            "descricao": " ".join(p for p in cabecalho if p) + (" " + descricao if descricao else ""),
            "publicado_em": criada.isoformat() if criada else "",
        })
    return jobs

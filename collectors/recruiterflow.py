"""
Coletor do Recruiterflow, o ATS da HireLATAM (agência que coloca latino-
americanos em empresas dos EUA). Confirmado em 08/11/2026:

    GET https://recruiterflow.com/<conta>/jobs
        -> HTML com a lista inteira num <script>:
           window.jobsList = {"department": [[nome, [vagas...]], ...],
                              "group": [...], "location": [...]};

Cada vaga: job_id, job_name, apply_link ("hirelatam/jobs/1809"), details
(países separados por vírgula: "Mexico , Colombia, Brazil, Latin America"),
remote_type ("Remote" ou null), employment_type, last_opened (ISO).
"department" tem cada vaga uma vez; "location" repete uma por país.

A descrição fica na página da vaga, no JSON-LD (schema.org JobPosting). Toda
descrição da HireLATAM abre com o mesmo parágrafo da agência e fecha com um
"Application Disclaimer": o coletor corta os dois. A página fica no cache.

Config: sources.recruiterflow.empresas: [{empresa: "HireLATAM", id: "hirelatam"}].
"""
from __future__ import annotations

import json
import re

import requests

from . import _cache
from ._util import aceita_brasil, registrar_board, strip_html, title_matches

BASE = "https://recruiterflow.com"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128.0 Safari/537.36"}
_INICIO_REAL = re.compile(r"(Job Title\s*:|About Our Client|About the Client|About the Role)", re.I)
_FIM = re.compile(r"Application Disclaimer", re.I)


def ler_lista(html: str) -> list[dict]:
    """As vagas de window.jobsList, uma por job_id."""
    i = html.find("window.jobsList")
    if i < 0:
        return []
    resto = html[i + len("window.jobsList"):].lstrip()
    resto = resto[1:].lstrip() if resto.startswith("=") else resto
    try:
        dados, _ = json.JSONDecoder().raw_decode(resto)
    except ValueError:
        return []
    vagas, vistos = [], set()
    for chave in ("department", "group", "location"):
        for _nome, lista in dados.get(chave) or []:
            for v in lista or []:
                if v.get("job_id") not in vistos:
                    vistos.add(v.get("job_id"))
                    vagas.append(v)
        if vagas:
            break  # "department" já traz todas; os outros só se ele faltar
    return vagas


def ler_descricao(html: str) -> str:
    for bloco in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html, re.S | re.I):
        try:
            d = json.loads(bloco)
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("description"):
            texto = strip_html(d["description"])
            m = _INICIO_REAL.search(texto)
            if m:
                texto = texto[m.start():]
            m = _FIM.search(texto)
            return (texto[:m.start()] if m else texto).strip()
    return ""


def _paises(details: str) -> str:
    return ", ".join(p.strip() for p in (details or "").split(",") if p.strip())


def fetch_recruiterflow(id_: str, empresa: str = "", term_filter=None, limit: int | None = None,
                        fetch_descriptions: bool = True) -> list[dict]:
    resp = requests.get(f"{BASE}/{id_}/jobs", headers=UA, timeout=30)
    resp.raise_for_status()
    vagas = ler_lista(resp.text)
    nome = empresa or id_
    registrar_board("recruiterflow", nome, len(vagas))
    jobs = []
    for v in vagas:
        if limit is not None and len(jobs) >= limit:
            break
        titulo = (v.get("job_name") or "").strip()
        if not titulo or (term_filter is not None and not title_matches(titulo, term_filter)):
            continue
        # remote_type às vezes vem null em vaga remota ("Remote Head of Marketing")
        remoto = (v.get("remote_type") or "").lower() == "remote" or titulo.lower().startswith("remote ")
        local = _paises(v.get("details"))
        # remota: os países decidem; presencial: o filtro de fora do Brasil do screen.py
        if remoto and not aceita_brasil(local or "Remote"):
            continue
        caminho = v.get("apply_link") or f"{id_}/jobs/{v.get('job_id')}"
        link = f"{BASE}/{caminho}"

        def baixar(url=link):
            try:
                return ler_descricao(requests.get(url, headers=UA, timeout=30).text)
            except requests.RequestException:
                return ""

        descricao = _cache.descricao(f"recruiterflow:{link}", baixar) if fetch_descriptions else ""
        jobs.append({
            "id": f"recruiterflow:{id_}:{v.get('job_id')}",
            "fonte": "recruiterflow",
            "titulo": titulo,
            "empresa": nome,
            "cidade": f"Remoto — {local}" if remoto and local else local,
            "estado": "",
            "remoto": remoto,
            "link": link,
            "descricao": descricao,
            "publicado_em": (v.get("last_opened") or "")[:10],
        })
    return jobs

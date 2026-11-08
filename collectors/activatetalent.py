"""
Coletor da Activate Talent (agência de staffing remoto que coloca gente de
LATAM, Filipinas, África do Sul etc. em marcas de e-commerce dos EUA: Amazon,
TikTok Shop, Shopify, wholesale). Plataforma própria, sem ATS conhecido.
Confirmado em 08/11/2026 (sem robots.txt nem sitemap: os dois dão a página 404):

    GET https://activatetalent.app/careers
        -> página Next.js (App Router). A tela mostra ~75 vagas e esconde o resto
           atrás dos botões "Show all N ... roles", mas o payload RSC
           (self.__next_f.push) traz a lista inteira, ~331 vagas:
           "positions":[{"id", "jobTitle", "department", "sourcingLocation",
                         "employmentType", "employmentLabel", "seniorityLevel",
                         "workSchedule"}, ...]

    GET https://activatetalent.app/careers/<id>
        -> JSON-LD (schema.org JobPosting) com description (texto puro) e
           datePosted. applicantLocationRequirements vem sempre "United States"
           (é o país do cliente, não do candidato): o coletor ignora.

sourcingLocation é de onde a agência contrata: "Global", "LATAM", "LatAm",
"Global, South Africa, Europe", "Philippines", "colombia or argentina", ou null
(30 vagas). A vaga entra se ALGUM dos locais aceita Brasil (Global, LATAM,
Brazil...); com null, decide a descrição. Todas as vagas são remotas
(jobLocationType TELECOMMUTE). A página da vaga só é baixada para as que passam
no título e no local, com pausa entre elas, e fica no cache.
"""
from __future__ import annotations

import json
import re
import time

import requests

from . import _cache
from ._util import aceita_brasil, registrar_board, strip_html, title_matches

BASE = "https://activatetalent.app"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128.0 Safari/537.36"}
_PUSH_RE = re.compile(r'self\.__next_f\.push\(\[1,\s*("(?:[^"\\]|\\.)*")\]\)', re.S)
_SEPARA_LOCAL = re.compile(r",|/|;|\bor\b|\band\b|\(|\)", re.I)


def _payload_rsc(html: str) -> str:
    """O payload RSC do Next.js, juntando os pedaços de self.__next_f.push."""
    partes = []
    for literal in _PUSH_RE.findall(html):
        try:
            partes.append(json.loads(literal))
        except ValueError:
            continue
    return "".join(partes)


def ler_lista(html: str) -> list[dict]:
    """Todas as vagas do array "positions" (uma por id)."""
    texto = _payload_rsc(html) or html
    vagas, vistos = [], set()
    for m in re.finditer(r'"positions"\s*:\s*\[', texto):
        try:
            lista, _ = json.JSONDecoder().raw_decode(texto[m.end() - 1:])
        except ValueError:
            continue
        for v in lista:
            if isinstance(v, dict) and v.get("id") and v.get("jobTitle") and v["id"] not in vistos:
                vistos.add(v["id"])
                vagas.append(v)
    return vagas


def locais_aceitos(local: str) -> list[str]:
    """Os pedaços de sourcingLocation que aceitam Brasil ("Global", "LATAM"...)."""
    return [p.strip() for p in _SEPARA_LOCAL.split(local or "") if p.strip() and aceita_brasil(p.strip())]


def ler_vaga(html: str) -> tuple[str, str]:
    """(descrição, data de publicação) do JSON-LD da página da vaga."""
    for bloco in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html, re.S | re.I):
        try:
            d = json.loads(bloco)
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("description"):
            return strip_html(d["description"]), (d.get("datePosted") or "")[:10]
    return "", ""


def fetch_activatetalent(term_filter=None, limit: int | None = None, fetch_descriptions: bool = True,
                         pausa: float = 1.0) -> list[dict]:
    resp = requests.get(f"{BASE}/careers", headers=UA, timeout=30)
    resp.raise_for_status()
    vagas = ler_lista(resp.text)
    registrar_board("activatetalent", "Activate Talent", len(vagas))
    jobs, baixadas = [], 0
    for v in vagas:
        if limit is not None and len(jobs) >= limit:
            break
        titulo = (v.get("jobTitle") or "").strip()
        if not titulo or (term_filter is not None and not title_matches(titulo, term_filter)):
            continue
        local = (v.get("sourcingLocation") or "").strip()
        aceitos = locais_aceitos(local)
        if local and not aceitos:
            continue  # "Philippines", "Europe", "colombia or argentina"...
        link = f"{BASE}/careers/{v['id']}"

        def baixar(url=link):
            nonlocal baixadas
            if baixadas and pausa:
                time.sleep(pausa)  # site pequeno: uma página por vez, sem martelar
            baixadas += 1
            try:
                r = requests.get(url, headers=UA, timeout=30)
                r.raise_for_status()
            except requests.RequestException:
                return None
            desc, data = ler_vaga(r.text)
            return {"descricao": desc, "publicado_em": data} if desc else None

        pagina = {}
        if fetch_descriptions:
            pagina = _cache.obter(f"activatetalent:{link}") or {}
            if not pagina:
                pagina = baixar() or {}
                if pagina:
                    _cache.guardar(f"activatetalent:{link}", pagina)
        desc = pagina.get("descricao", "")
        # sem local na lista: só fica se a descrição citar Brasil/LATAM/anywhere
        if not local and not aceita_brasil("", desc):
            continue
        cabecalho = [f"Área: {v['department']}." if v.get("department") else "",
                     f"Senioridade: {v['seniorityLevel'].strip()}." if v.get("seniorityLevel") else "",
                     f"Contrato: {v['employmentLabel']}." if v.get("employmentLabel") else "",
                     f"Horário: {v['workSchedule']}." if v.get("workSchedule") else "",
                     f"Contrata em: {local}." if local else "",
                     "Vaga remota pela Activate Talent (agência de staffing; cliente nos EUA)."]
        jobs.append({
            "id": f"activatetalent:{v['id']}",
            "fonte": "activatetalent",
            "titulo": titulo,
            "empresa": "Activate Talent",
            "cidade": "Remoto — " + ", ".join(aceitos) if aceitos else "Remoto",
            "estado": "",
            "remoto": True,
            "link": link,
            "descricao": " ".join(p for p in cabecalho if p) + (" " + desc if desc else ""),
            "publicado_em": pagina.get("publicado_em", ""),
        })
    return jobs

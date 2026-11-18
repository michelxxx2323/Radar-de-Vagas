"""
Coletor do Jobgether (https://jobgether.com), board de vagas remotas. O site
tem uma API pública de busca feita para agentes de IA, sem chave, e o
robots.txt libera justamente esse caminho (o resto com "?" fica proibido).
Confirmado em 18/11/2026:

    GET https://jobgether.com/astroapi/ai/jobs.json
        ?keyword=<termo>&locations=brazil,latam,anywhere&sort=date&limit=25&page=<n>
        -> {"jobs": [{"id", "title", "company", "url", "location", "remote",
                      "contractType", "experience", "salaryRange",
                      "jobFunctions", "postedAt"}],
            "pagination": {"page", "limit", "hasMore"}}

    (documentação: /astroapi/ai/jobs/docs; até 25 por página e 10 páginas.
    O caminho "oficial" /api/v1/jobs é o mesmo, mas o robots só libera o alias.)

`keyword` busca no título, na função, nas skills e no nome da empresa; os
termos do Perfil 2 trazem de 0 a ~25 vagas cada. `locations` aceita slugs de
país e região ("brazil", "latam", "anywhere"; "latin-america" dá 400). Por
padrão só vem vaga "Full Remote".

O `location` da lista é o nome das regiões e países aceitos ("South America,
Latin America, Brazil", "Anywhere"). A página da vaga (/offer/<id>-<slug>) traz
o JSON-LD JobPosting com a descrição em HTML e `applicantLocationRequirements`
com os países aceitos em código ISO (a região já vem expandida: "Latin America"
= 22 países, com BR; "Anywhere" = o mundo todo). A vaga fica se:
  1. o título passa no filtro do perfil;
  2. o `location` aceita Brasil (Brasil/LATAM/Américas ou anywhere sem país);
  3. a lista de países do JSON-LD, quando existe, tem "BR".
A página só é baixada para quem passa em 1 e 2, uma por vez, com pausa, e fica
no cache (output/descricoes.json) por 7 dias.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import threading
import time

import requests

from . import _cache
from ._util import aceita_brasil, registrar_board, strip_html, title_matches

BASE = "https://jobgether.com"
API_URL = f"{BASE}/astroapi/ai/jobs.json"
UA = {"User-Agent": "Mozilla/5.0 (compatible; job-fit-screener/1.0)", "Accept": "application/json"}
UA_PAGINA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128.0 Safari/537.36"}
POR_PAGINA = 25
LOCAIS_PADRAO = ["brazil", "latam", "anywhere"]

# as buscas rodam em paralelo (uma por termo); as páginas de vaga, uma por vez
_LOCK_PAGINA = threading.Lock()
_ULTIMA_PAGINA = [0.0]


def ler_vaga(html: str) -> dict:
    """{"descricao", "paises", "empresa", "publicado_em"} do JSON-LD da página
    da vaga. `paises` é a lista de códigos ISO aceitos ([] se a página não diz)."""
    for bloco in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html, re.S | re.I):
        try:
            d = json.loads(bloco)
        except ValueError:
            continue
        for item in (d if isinstance(d, list) else d.get("@graph") or [d]):
            if not isinstance(item, dict) or item.get("@type") != "JobPosting":
                continue
            req = item.get("applicantLocationRequirements") or []
            if isinstance(req, dict):
                req = [req]
            paises = [str(r.get("name") or "").strip().upper() for r in req if isinstance(r, dict) and r.get("name")]
            org = item.get("hiringOrganization") or {}
            return {
                "descricao": strip_html(item.get("description") or ""),
                "paises": paises,
                "empresa": (org.get("name") or "").strip() if isinstance(org, dict) else "",
                "publicado_em": str(item.get("datePosted") or "")[:10],
            }
    return {}


def aceita_br_pelos_paises(paises: list[str]) -> bool:
    """Lista vazia não decide (a página não disse); com lista, precisa ter o Brasil."""
    return not paises or any(p in ("BR", "BRA", "BRAZIL", "BRASIL") for p in paises)


def _salario(faixa: str) -> str:
    """"130000-150000 USD" -> "USD 130,000 a 150,000 /ano"."""
    m = re.match(r"\s*(\d+)\s*-\s*(\d+)\s*([A-Z]{3})?\s*$", faixa or "")
    if m:
        lo, hi, moeda = int(m.group(1)), int(m.group(2)), m.group(3) or "USD"
        return f"{moeda} {lo:,} a {hi:,} /ano" if lo != hi else f"{moeda} {lo:,} /ano"
    return (faixa or "").strip()


def _baixar_pagina(url: str, pausa: float) -> dict:
    with _LOCK_PAGINA:
        espera = pausa - (time.monotonic() - _ULTIMA_PAGINA[0])
        if _ULTIMA_PAGINA[0] and espera > 0:
            time.sleep(espera)  # uma página por vez, sem martelar o site
        try:
            r = requests.get(url, headers=UA_PAGINA, timeout=30)
            r.raise_for_status()
        except requests.RequestException:
            return {}
        finally:
            _ULTIMA_PAGINA[0] = time.monotonic()
    return ler_vaga(r.text)


def _buscar(params: dict, tentativas: int = 2, espera: float = 5.0) -> dict:
    """Uma página da API. 504 (a busca estourou o tempo do servidor), outro 5xx
    ou timeout: espera e tenta de novo uma vez. Visto na rodada real de
    18/11/2026: "business operations" deu 504 e passou na 2ª tentativa."""
    for tentativa in range(tentativas):
        try:
            resp = requests.get(API_URL, params=params, headers=UA, timeout=30)
            if resp.status_code < 500 or tentativa == tentativas - 1:
                resp.raise_for_status()
                return resp.json() or {}
        except (requests.Timeout, requests.ConnectionError):
            if tentativa == tentativas - 1:
                raise
        time.sleep(espera)
    return {}


def fetch_jobgether(term: str, locations=None, term_filter=None, max_pages: int = 3,
                    limit: int | None = None, fetch_descriptions: bool = True, pausa: float = 1.0,
                    max_dias: int | None = None) -> list[dict]:
    locais = ",".join(locations or LOCAIS_PADRAO)
    jobs: list[dict] = []
    total = 0
    for pagina in range(1, max_pages + 1):
        params = {"keyword": term, "locations": locais, "sort": "date", "limit": POR_PAGINA, "page": pagina}
        dados = _buscar(params)
        lote = dados.get("jobs") or []
        total += len(lote)
        for v in lote:
            if limit is not None and len(jobs) >= limit:
                break
            titulo = (v.get("title") or "").strip()
            link = (v.get("url") or "").strip()
            if not titulo or not link or (term_filter is not None and not title_matches(titulo, term_filter)):
                continue
            remoto = (v.get("remote") or "Full Remote").strip()
            if "hybrid" in remoto.lower():
                continue  # vaga híbrida não é remota (regra do Michel, 11/11/2026)
            local = (v.get("location") or "").strip()
            if not aceita_brasil(local):
                continue
            publicado = str(v.get("postedAt") or "")[:10]
            if max_dias and publicado:
                try:
                    if (dt.date.today() - dt.date.fromisoformat(publicado)).days > max_dias:
                        continue
                except ValueError:
                    pass
            pag = {}
            if fetch_descriptions:
                chave = f"jobgether:{v.get('id') or link}"
                pag = _cache.obter(chave) or {}
                if not pag:
                    pag = _baixar_pagina(link, pausa)
                    if pag.get("descricao"):
                        _cache.guardar(chave, pag)
                if not aceita_br_pelos_paises(pag.get("paises") or []):
                    continue  # ex.: "Latin America, Mexico" que na página só aceita MX
            cabecalho = [f"Contrato: {v['contractType']}." if v.get("contractType") else "",
                         f"Experiência: {v['experience']}." if v.get("experience") else "",
                         f"Modelo: {remoto}.",
                         f"Aceita candidatos de: {local}." if local else "",
                         f"Salário: {_salario(v['salaryRange'])}." if v.get("salaryRange") else ""]
            descricao = pag.get("descricao") or ""
            jobs.append({
                "id": f"jobgether:{v.get('id') or link}",
                "fonte": "jobgether",
                "titulo": titulo,
                "empresa": (v.get("company") or pag.get("empresa") or "").strip(),
                "cidade": f"Remoto internacional — {local or 'local não informado'}",
                "estado": "",
                "remoto": True,
                "link": link,
                "descricao": " ".join(p for p in cabecalho if p) + (" " + descricao if descricao else ""),
                "publicado_em": publicado or pag.get("publicado_em", ""),
                "salario": _salario(v.get("salaryRange") or ""),
            })
        if limit is not None and len(jobs) >= limit:
            break
        if not (dados.get("pagination") or {}).get("hasMore") or len(lote) < POR_PAGINA:
            break
        time.sleep(1)  # API gratuita, sem martelar
    # termo sem nenhuma vaga é normal aqui ("clay" dá 0); só registra quando trouxe
    # algo, para o aviso de "board zerado" não virar ruído
    if total:
        registrar_board("jobgether", term, total)
    return jobs[:limit] if limit is not None else jobs

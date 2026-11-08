"""
Coletor do TrackerRMS, o ATS da Proppel (agência que coloca gente de LATAM em
marcas DTC dos EUA e do Reino Unido: Jones Road Beauty, Pvolve, Cheers,
Mugsy), pelo portal público "Lite". Confirmado em 08/11/2026 (sem robots.txt;
não há RSS nem JSON: /Opportunity/Rss dá 500):

    GET https://evoportalus.tracker-rms.com/Opportunity/Lite?db=<conta>
        -> todas as vagas numa página só (39 na Proppel), cada uma num
           <div class="fj_post home3"> com <h3> (título), "Posted on
           <span class="fw600">02 November 26</span> by ...", um resumo em
           <p> cuja 1ª linha é "Location: LATAM" (ou "US (Authorized workers
           only)", "Argentina, Brazil, Colombia, and Mexico"...) e o link
           /Opportunity/LiteJob?db=<conta>&id=<n>. O salário vem sempre "$ - $".

    GET .../Opportunity/LiteJob?db=<conta>&id=<n>
        -> <h2 class="color-white"> (título) e a descrição em
           div.candidate_about_info (com entidades cp1252, ex. &#146;).

A vaga fica se o "Location:" aceitar Brasil; sem essa linha, decide a
descrição. A página da vaga só é baixada para as que passam no título e no
local, com pausa, e fica no cache.

Config: sources.trackerrms.empresas: [{empresa: "Proppel", id: "weareproppel"}]
(host opcional, padrão evoportalus.tracker-rms.com).
"""
from __future__ import annotations

import datetime as dt
import html as _html
import re
import time

import requests
from bs4 import BeautifulSoup

from . import _cache
from ._util import aceita_brasil, registrar_board, strip_html, title_matches

HOST = "evoportalus.tracker-rms.com"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128.0 Safari/537.36"}
_LOCAL_RE = re.compile(r"^\s*Location\s*:\s*(.+?)\s*$", re.I | re.M)
_CP1252 = re.compile(r"&#(1[2-5]\d);")


def _texto(no) -> str:
    """Texto do nó, com quebra de linha entre blocos e as entidades cp1252
    (&#146; = ’) já trocadas."""
    if no is None:
        return ""
    bruto = _CP1252.sub(lambda m: bytes([int(m.group(1))]).decode("cp1252", "replace"), str(no))
    return BeautifulSoup(_html.unescape(bruto), "html.parser").get_text("\n", strip=True)


def _data(texto: str) -> str:
    m = re.search(r"(\d{1,2} [A-Za-z]+ \d{2,4})", texto or "")
    if not m:
        return ""
    for fmt in ("%d %B %y", "%d %B %Y", "%d %b %y", "%d %b %Y"):
        try:
            return dt.datetime.strptime(m.group(1), fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def ler_lista(html: str, host: str = HOST) -> list[dict]:
    sopa = BeautifulSoup(html, "html.parser")
    vagas, vistos = [], set()
    for bloco in sopa.select("div.fj_post"):
        a = bloco.find("a", href=re.compile(r"LiteJob", re.I))
        h3 = bloco.find("h3")
        if not a or not h3:
            continue
        href = a["href"]
        link = href if href.startswith("http") else f"https://{host}/{href.lstrip('/')}"
        m = re.search(r"[?&]id=(\d+)", link)
        if not m or m.group(1) in vistos:
            continue
        vistos.add(m.group(1))
        resumo = ""
        for p in bloco.find_all("p"):
            t = _texto(p)
            if t and not t.lower().startswith("posted on"):
                resumo = t
                break
        postado = next((p for p in bloco.find_all(["p", "li", "span", "div"])
                        if "posted on" in p.get_text(" ").lower()), None)
        local = _LOCAL_RE.search(resumo)
        vagas.append({
            "id": m.group(1),
            "titulo": h3.get_text(" ", strip=True),
            "local": local.group(1).strip() if local else "",
            "resumo": resumo,
            "publicado_em": _data(postado.get_text(" ", strip=True)) if postado else "",
            "link": link,
        })
    return vagas


def ler_vaga(html: str) -> str:
    sopa = BeautifulSoup(html, "html.parser")
    corpo = sopa.select_one("div.candidate_about_info")
    return strip_html(_texto(corpo)) if corpo else ""


def fetch_trackerrms(id_: str, empresa: str = "", term_filter=None, limit: int | None = None,
                     host: str = HOST, fetch_descriptions: bool = True, pausa: float = 1.0) -> list[dict]:
    resp = requests.get(f"https://{host}/Opportunity/Lite", params={"db": id_}, headers=UA, timeout=30)
    resp.raise_for_status()
    vagas = ler_lista(resp.text, host)
    nome = empresa or id_
    registrar_board("trackerrms", nome, len(vagas))
    jobs, baixadas = [], 0
    for v in vagas:
        if limit is not None and len(jobs) >= limit:
            break
        if not v["titulo"] or (term_filter is not None and not title_matches(v["titulo"], term_filter)):
            continue
        if v["local"] and not aceita_brasil(v["local"]):
            continue  # "US (Authorized workers only)", "Argentina, Colombia"...
        desc = ""
        if fetch_descriptions:
            chave = f"trackerrms:{v['link']}"
            desc = (_cache.obter(chave) or {}).get("descricao", "")
            if not desc:
                if baixadas and pausa:
                    time.sleep(pausa)
                baixadas += 1
                try:
                    r = requests.get(v["link"], headers=UA, timeout=30)
                    r.raise_for_status()
                    desc = ler_vaga(r.text)
                except requests.RequestException:
                    desc = ""
                if desc:
                    _cache.guardar(chave, {"descricao": desc})
        desc = desc or strip_html(v["resumo"])
        if not v["local"] and not aceita_brasil("", f"{v['titulo']} {desc}"):
            continue
        jobs.append({
            "id": f"trackerrms:{id_}:{v['id']}",
            "fonte": "trackerrms",
            "titulo": v["titulo"],
            "empresa": nome,
            "cidade": f"Remoto — {v['local']}" if v["local"] else "Remoto",
            "estado": "",
            "remoto": True,
            "link": v["link"],
            "descricao": f"Vaga pela {nome} (agência de staffing). " + desc,
            "publicado_em": v["publicado_em"],
        })
    return jobs

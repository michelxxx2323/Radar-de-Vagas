"""
Coletor do GTM Daily (https://gtmdaily.com/jobs), diretório curado de vagas de
GTM engineering, RevOps, sales/marketing ops e outbound — a fonte nº 1 do
fontes-vagas.md (07/11/2026).

A página é renderizada no servidor (Next.js): 20 cards por página em
/jobs?page=N ("Page 1 of 15" em 07/11/2026). Cada card é um <article> com:
  - local:      <span class="absolute left-3 top-3 ...">Remote, Brazil</span>
  - salário:    <p class="text-xs ...">$190K to $230K a year · Full-time</p>
  - título:     <h3><a href="/jobs/<slug>?via=list">GTM Engineer</a></h3>
  - ferramentas <p class="line-clamp-1 ...">Clay, n8n</p> (pode faltar)
  - empresa:    <span class="truncate">Tremendous</span>
  - idade:      <span class="shrink-0 text-subtle">Yesterday</span>
A descrição fica na página da vaga, em <section aria-labelledby="role-h">.

robots.txt: proíbe URL com 2+ parâmetros (/jobs?*&*) e /jobs/go (o link de
candidatura). Por isso só /jobs?page=N (um parâmetro) e o link salvo é a
página da vaga no GTM Daily. Não há filtro de local na URL: o local do card
decide. Card com local claramente de fora ("Remote, New York, NY",
"San Francisco") cai antes de baixar a página; "Remote" sem país baixa a
página (fica no cache de descrições) e só fica se a descrição aceitar Brasil.
"""
from __future__ import annotations

import datetime as dt
import re
import time

import requests

from . import _cache
from ._util import _LOCAL_BR, _LOCAL_FORTE, _fold, aceita_brasil, registrar_board, strip_html, title_matches

BASE = "https://gtmdaily.com"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128.0 Safari/537.36"}


def idade_para_data(texto: str, hoje: dt.date | None = None) -> str:
    """"Today", "Yesterday", "3 days ago", "2 weeks ago", "1 month ago" -> ISO."""
    hoje = hoje or dt.date.today()
    t = (texto or "").strip().lower()
    if t in ("today", "just now") or t.endswith(("hours ago", "hour ago", "minutes ago", "minute ago")):
        return hoje.isoformat()
    if t == "yesterday":
        return (hoje - dt.timedelta(days=1)).isoformat()
    m = re.fullmatch(r"(\d+|a|an) (day|week|month)s? ago", t)
    if not m:
        return ""
    n = 1 if m.group(1) in ("a", "an") else int(m.group(1))
    dias = {"day": 1, "week": 7, "month": 30}[m.group(2)] * n
    return (hoje - dt.timedelta(days=dias)).isoformat()


def ler_cards(html: str) -> tuple[list[dict], bool]:
    """(cards da página, tem próxima página?)."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    cards = []
    for art in soup.find_all("article"):
        a = art.select_one("h3 a[href^='/jobs/']")
        if not a:
            continue
        local = art.select_one("span.absolute.left-3")
        pay = art.select_one("p.text-xs")
        tools = art.select_one("p.line-clamp-1")
        empresa = art.select_one("span.truncate")
        idade = art.select_one("span.shrink-0.text-subtle")
        cards.append({
            "titulo": a.get_text(" ", strip=True),
            "link": BASE + a["href"].split("?")[0],
            "local": local.get_text(" ", strip=True) if local else "",
            "pay": pay.get_text(" ", strip=True) if pay else "",
            "ferramentas": tools.get_text(" ", strip=True) if tools else "",
            "empresa": empresa.get_text(" ", strip=True) if empresa else "",
            "idade": idade.get_text(" ", strip=True) if idade else "",
        })
    proxima = bool(soup.find("a", href=re.compile(r"^/jobs\?page=\d+$"), string=re.compile(r"Older", re.I)))
    return cards, proxima


def ler_descricao(html: str) -> str:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    sec = soup.select_one("section[aria-labelledby='role-h']") or soup.find("article")
    return strip_html(str(sec)) if sec else ""


def _local_de_fora(local: str) -> bool:
    """Card cujo local já diz que não aceita Brasil (não baixa a página)."""
    loc = _fold(local)
    if _LOCAL_FORTE.search(loc) or _LOCAL_BR.search(loc):
        return False
    resto = re.sub(r"\b(remote|remoto|hybrid|onsite|on-site|anywhere|worldwide|global)\b|[,;/()-]", " ", loc).strip()
    return bool(resto)  # sobrou cidade/país que não é Brasil/LATAM


def _get(url: str, params: dict | None = None):
    resp = requests.get(url, params=params, headers=UA, timeout=30)
    resp.raise_for_status()
    return resp


def fetch_gtmdaily(term_filter=None, max_pages: int = 15, limit: int | None = None,
                   pausa: float = 1.0) -> list[dict]:
    cards: list[dict] = []
    for pagina in range(1, max_pages + 1):
        html = _get(BASE + "/jobs", {"page": pagina} if pagina > 1 else None).text
        lote, proxima = ler_cards(html)
        cards.extend(lote)
        if not proxima or not lote:
            break
        time.sleep(pausa)  # site pequeno — sem martelar
    registrar_board("gtmdaily", "gtmdaily", len(cards))

    jobs = []
    for c in cards:
        if limit is not None and len(jobs) >= limit:
            break
        if term_filter is not None and not title_matches(c["titulo"], term_filter):
            continue
        if _local_de_fora(c["local"]):
            continue

        def baixar(link=c["link"]):
            try:
                return ler_descricao(_get(link).text)
            except requests.RequestException:
                return ""

        descricao = _cache.descricao(f"gtmdaily:{c['link']}", baixar)
        if not aceita_brasil(c["local"], descricao) and not _LOCAL_BR.search(_fold(c["local"])):
            continue
        partes_pay = [p.strip() for p in c["pay"].split("·")]
        salario = partes_pay[0] if len(partes_pay) > 1 or "$" in c["pay"] else ""
        if c["ferramentas"]:
            descricao = f"Ferramentas citadas: {c['ferramentas']}. {descricao}"
        loc = _fold(c["local"])
        jobs.append({
            "id": f"gtmdaily:{c['link']}",
            "fonte": "gtmdaily",
            "titulo": c["titulo"],
            "empresa": c["empresa"],
            "cidade": c["local"],
            "estado": "",
            "remoto": "remote" in loc or "anywhere" in loc,
            "link": c["link"],
            "descricao": descricao,
            "publicado_em": idade_para_data(c["idade"]),
            "salario": salario,
        })
    return jobs

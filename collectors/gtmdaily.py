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

A listagem só vai até a página 5 (da 6 em diante o site devolve a 5 de novo).
As vagas que não couberam nela vêm do /sitemap.xml: baixa a página da vaga
(local, salário e ferramentas ficam no <dl> do quadro lateral) e guarda no
cache, então só a 1ª rodada (ou uma vaga nova) baixa a página.

Filtros do site (role=gtm-engineer, tool=Clay, arrangement=remote) não servem:
combinar dois é URL com 2 parâmetros, que o robots.txt proíbe, e sem o
arrangement cada filtro só mostra a 1ª página (a paginação seria o 2º
parâmetro). Por isso o coletor lê todas as vagas e filtra aqui: fica a vaga com
título do perfil (title_keywords) OU com uma das `ferramentas` (Clay, n8n,
Claude...), e o local decide o remoto que aceita Brasil.
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


def ler_pagina_vaga(html: str) -> dict:
    """Campos de uma página de vaga, para as vagas que só aparecem no sitemap.
    O quadro lateral é um <dl> com <dt>Location</dt><dd>Remote, Brazil</dd>,
    Pay, Type, Tools...; o título é o <h1> e a empresa o <p> logo acima dele."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    campos = {}
    for dt_ in soup.select("dl dt"):
        dd = dt_.find_next_sibling("dd")
        if dd:
            campos[dt_.get_text(" ", strip=True).lower()] = dd.get_text(", ", strip=True)
    h1 = soup.find("h1")
    empresa = h1.find_previous_sibling() if h1 else None
    pay = campos.get("pay", "")
    if pay.lower().startswith("not published"):
        pay = ""
    return {
        "titulo": h1.get_text(" ", strip=True) if h1 else "",
        "empresa": empresa.get_text(" ", strip=True) if empresa else "",
        "local": campos.get("location", ""),
        "pay": " · ".join(p for p in (pay, campos.get("type", "")) if p),
        "ferramentas": campos.get("tools", ""),
        "publicado": campos.get("originally posted", ""),
        "descricao": ler_descricao(html),
    }


def _data_postada(texto: str) -> str:
    """"Sep 25, 2026" -> "2026-09-25"."""
    try:
        return dt.datetime.strptime(texto.strip(), "%b %d, %Y").date().isoformat()
    except ValueError:
        return ""


def links_do_sitemap() -> list[str]:
    """Todas as vagas do site. A listagem /jobs?page=N para na página 5 (pedir
    a 6 ou a 15 devolve a 5 de novo, apesar do "Page 1 of 15" — visto em
    07/11/2026), então as vagas mais antigas, como a da Wellhub, só aparecem
    aqui (~255 em 07/11/2026)."""
    xml = _get(BASE + "/sitemap.xml").text
    return [BASE + "/jobs/" + s for s in re.findall(r"<loc>https://gtmdaily\.com/jobs/([^<?/]+)</loc>", xml)]


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


def _usa_ferramenta(ferramentas_vaga: str, ferramentas: list[str] | None) -> bool:
    if not ferramentas or not ferramentas_vaga:
        return False
    texto = _fold(ferramentas_vaga)
    return any(re.search(rf"\b{re.escape(_fold(f))}\b", texto) for f in ferramentas)


def fetch_gtmdaily(term_filter=None, max_pages: int = 15, limit: int | None = None,
                   pausa: float = 1.0, usar_sitemap: bool = True,
                   ferramentas: list[str] | None = None) -> list[dict]:
    """`ferramentas`: vaga que cita uma delas fica mesmo com título fora do
    term_filter (ex.: "Marketing Technologist" com Clay)."""
    cards: list[dict] = []
    vistos: set[str] = set()
    for pagina in range(1, max_pages + 1):
        html = _get(BASE + "/jobs", {"page": pagina} if pagina > 1 else None).text
        lote, proxima = ler_cards(html)
        novos = [c for c in lote if c["link"] not in vistos]
        vistos.update(c["link"] for c in novos)
        cards.extend(novos)
        if not proxima or not novos:  # a partir da 6ª o site repete a 5ª
            break
        time.sleep(pausa)  # site pequeno — sem martelar

    if usar_sitemap:
        try:
            faltam = [u for u in links_do_sitemap() if u not in vistos]
        except requests.RequestException:
            faltam = []
        for link in faltam:
            slug = link.rsplit("/", 1)[-1]
            # o slug é "<título>-<empresa>-<hash>": sem filtro por ferramenta,
            # filtra pelo título antes de baixar; com ele, precisa da página
            # (as ferramentas só aparecem nela), que fica no cache
            if not ferramentas and term_filter is not None and not (
                    title_matches(slug, term_filter) or title_matches(slug.replace("-", " "), term_filter)):
                continue
            det = _cache.obter(f"gtmdaily-pagina:{link}")
            if det is None:
                try:
                    det = ler_pagina_vaga(_get(link).text)
                except requests.RequestException:
                    continue
                if det["titulo"]:
                    _cache.guardar(f"gtmdaily-pagina:{link}", det)
                time.sleep(pausa)
            if not det.get("titulo"):
                continue
            vistos.add(link)
            cards.append({"titulo": det["titulo"], "link": link, "local": det["local"], "pay": det["pay"],
                          "ferramentas": det["ferramentas"], "empresa": det["empresa"], "idade": "",
                          "publicado": det["publicado"], "descricao": det["descricao"]})
    registrar_board("gtmdaily", "gtmdaily", len(cards))

    jobs = []
    for c in cards:
        if limit is not None and len(jobs) >= limit:
            break
        if term_filter is not None and not title_matches(c["titulo"], term_filter) \
                and not _usa_ferramenta(c["ferramentas"], ferramentas):
            continue
        if _local_de_fora(c["local"]):
            continue

        def baixar(link=c["link"]):
            try:
                return ler_descricao(_get(link).text)
            except requests.RequestException:
                return ""

        descricao = c.get("descricao") or _cache.descricao(f"gtmdaily:{c['link']}", baixar)
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
            "publicado_em": idade_para_data(c["idade"]) or _data_postada(c.get("publicado", "")),
            "salario": salario,
        })
    return jobs

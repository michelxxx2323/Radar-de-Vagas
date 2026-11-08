"""
Coletor da Constant Hire (recrutadora que só trabalha para marcas DTC e de
e-commerce: Starface, HigherDOSE, ARMRA, Native...). Site em Webflow; o ATS
por trás é o Stardex, mas o HTML do site é mais simples. Confirmado em
08/11/2026 (robots.txt só tem a linha do Sitemap):

    GET https://www.constanthire.com/ecommerce-jobs
        -> a lista inteira (46 vagas) no HTML, sem paginação. Cada vaga é um
           <div class="job w-dyn-item" job-workspace="REMOTE|HYBRID|ON_SITE"
           job-name="..." job-location-country="USA|LATAM|LATAM; Eastern Europe"
           job-location-city="..." job-category="...">, com <h3 class="job-title">,
           <div job-added-date="2026-10-16">, o cliente em div.opcacity-80 e
           o link <a job-url href="/ecommerce-jobs/<slug>">.

    GET https://www.constanthire.com/ecommerce-jobs/<slug>
        -> <div class="structured-attributes" job-id="CH-2026-82"
           job-added-date job-valid-through job-employment-type
           job-salary-min job-salary-max job-salary-currency job-salary-unit
           job-apply-url="https://jobs.stardex.com/constant-hire/<slug>" ...>
           e a descrição em <div job-description class="... w-richtext">.
           O JSON-LD é montado por script no navegador: não está no HTML.

Em 08/11/2026: 26 remotas nos EUA, 7 remotas LATAM, 12 híbridas/presenciais
em cidades dos EUA. Fica só a remota cujo país aceita Brasil (LATAM); a página
da vaga só é baixada para essas, com pausa, e fica no cache.
"""
from __future__ import annotations

import time

import requests
from bs4 import BeautifulSoup

from . import _cache
from ._util import aceita_brasil, registrar_board, strip_html, title_matches

BASE = "https://www.constanthire.com"
LISTA = f"{BASE}/ecommerce-jobs"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128.0 Safari/537.36"}


def ler_lista(html: str) -> list[dict]:
    """As vagas da página /ecommerce-jobs (uma por link)."""
    sopa = BeautifulSoup(html, "html.parser")
    vagas, vistos = [], set()
    for card in sopa.select("div.w-dyn-item[job-workspace], div.job.w-dyn-item"):
        a = card.find("a", attrs={"job-url": True}) or card.find("a", href=True)
        href = (a.get("href") or "").strip() if a else ""
        if not href or href in vistos:
            continue
        vistos.add(href)
        h3 = card.select_one(".job-title")
        data = card.find(attrs={"job-added-date": True})
        cliente = card.select_one(".opcacity-80")
        vagas.append({
            "titulo": (h3.get_text(" ", strip=True) if h3 else card.get("job-name") or "").strip(),
            "modelo": (card.get("job-workspace") or "").strip().upper(),
            "pais": (card.get("job-location-country") or "").strip(),
            "cidade": (card.get("job-location-city") or "").strip(),
            "categoria": (card.get("job-category") or "").strip(),
            "cliente": cliente.get_text(" ", strip=True) if cliente else "",
            "publicado_em": (data.get("job-added-date") or "")[:10] if data else "",
            "link": href if href.startswith("http") else BASE + href,
        })
    return vagas


def ler_vaga(html: str) -> dict:
    """Descrição, data e salário da página da vaga."""
    sopa = BeautifulSoup(html, "html.parser")
    attrs = sopa.select_one("div.structured-attributes")
    a = attrs.attrs if attrs else {}
    desc = sopa.find(attrs={"job-description": True})
    salario = ""
    if a.get("job-salary-min") or a.get("job-salary-max"):
        faixa = "–".join(x for x in (a.get("job-salary-min"), a.get("job-salary-max")) if x)
        salario = " ".join(x for x in (a.get("job-salary-currency", ""), faixa, a.get("job-salary-unit", "")) if x)
    return {
        "descricao": strip_html(desc.decode_contents()) if desc else "",
        "publicado_em": (a.get("job-added-date") or "")[:10],
        "contrato": a.get("job-employment-type", ""),
        "salario": salario,
        "candidatura": a.get("job-apply-url", ""),
    }


def fetch_constanthire(term_filter=None, limit: int | None = None, fetch_descriptions: bool = True,
                       pausa: float = 1.0) -> list[dict]:
    resp = requests.get(LISTA, headers=UA, timeout=30)
    resp.raise_for_status()
    vagas = ler_lista(resp.text)
    registrar_board("constanthire", "Constant Hire", len(vagas))
    jobs, baixadas = [], 0
    for v in vagas:
        if limit is not None and len(jobs) >= limit:
            break
        if not v["titulo"] or (term_filter is not None and not title_matches(v["titulo"], term_filter)):
            continue
        # híbrida/presencial é sempre em cidade dos EUA; remota "USA" ou
        # "Eastern EU/UK" não aceita Brasil
        if v["modelo"] != "REMOTE" or not aceita_brasil(v["pais"]):
            continue
        pagina = {}
        if fetch_descriptions:
            pagina = _cache.obter(f"constanthire:{v['link']}") or {}
            if not pagina:
                if baixadas and pausa:
                    time.sleep(pausa)  # site pequeno: uma página por vez
                baixadas += 1
                try:
                    r = requests.get(v["link"], headers=UA, timeout=30)
                    r.raise_for_status()
                    pagina = ler_vaga(r.text)
                except requests.RequestException:
                    pagina = {}
                if pagina.get("descricao"):
                    _cache.guardar(f"constanthire:{v['link']}", pagina)
        cabecalho = [f"Cliente: {v['cliente']}." if v["cliente"] else "",
                     f"Área: {v['categoria']}." if v["categoria"] else "",
                     f"Contrato: {pagina['contrato']}." if pagina.get("contrato") else "",
                     f"Salário: {pagina['salario']}." if pagina.get("salario") else "",
                     f"Contrata em: {v['pais']}.",
                     "Vaga remota pela Constant Hire (recrutadora de marcas DTC/e-commerce dos EUA)."]
        job = {
            "id": f"constanthire:{v['link'].rstrip('/').rsplit('/', 1)[-1]}",
            "fonte": "constanthire",
            "titulo": v["titulo"],
            "empresa": "Constant Hire",
            "cidade": f"Remoto — {v['pais']}",
            "estado": "",
            "remoto": True,
            "link": v["link"],
            "descricao": " ".join(p for p in cabecalho if p) + (
                " " + pagina["descricao"] if pagina.get("descricao") else ""),
            "publicado_em": pagina.get("publicado_em") or v["publicado_em"],
        }
        if pagina.get("salario"):
            job["salario"] = pagina["salario"]
        jobs.append(job)
    return jobs

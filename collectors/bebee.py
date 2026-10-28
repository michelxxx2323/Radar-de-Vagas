"""
Coletor via scraping da busca pública da beBee (bebee.com/br/jobs?q=...).

A página é renderizada no servidor (SSR) — confirmado inspecionando a
resposta HTTP bruta em 21/09/2026 e de novo em 23/09/2026 (quando a
estrutura dos cards mudou e o parser foi refeito): o HTML já vem com os cards de vaga
prontos, então dá pra usar requests + BeautifulSoup, sem precisar de
navegador/Playwright.

IMPORTANTE: a beBee não tem API pública nem RSS — isso é scraping de HTML
best-effort, sujeito a mudanças na estrutura da página. Se os campos
começarem a vir vazios, rode debug_bebee.py pra inspecionar o HTML bruto de
um card e ajustar os seletores abaixo.
"""
from __future__ import annotations

import json
import re

import requests
from bs4 import BeautifulSoup

from ._util import strip_html

BEBEE_SEARCH_URL = "https://bebee.com/br/jobs"
_HEADERS = {"Accept": "text/html", "User-Agent": "Mozilla/5.0 (compatible; job-fit-screener/1.0)"}
_DATE_RE = re.compile(r"(Hoje|Ontem|Há \d+ dias?)")


def fetch_bebee(term: str, max_pages: int = 2) -> list[dict]:
    jobs = []
    seen_links = set()

    for page in range(1, max_pages + 1):
        params = {"q": term}
        if page > 1:
            params["page"] = page

        resp = requests.get(BEBEE_SEARCH_URL, params=params, headers=_HEADERS, timeout=20)
        if resp.status_code != 200:
            break
        soup = BeautifulSoup(resp.text, "html.parser")

        # Estrutura observada ao vivo em 23/09/2026 (a de 21/09, com o <a> envolvendo
        # o heading, deixou de existir): cada card é um <div class="p-4"> com
        #   <h3><a href="/br/jobs/...">título</a></h3>
        #   <span>(ícone lucide-map-pin) local</span>
        #   <span class="truncate">empresa</span>
        #   <p class="line-clamp-2">resumo</p>
        #   tags (tipo de contrato, "100% Remoto") e a data ("Ontem", "Há 3 dias").
        # Links de nav/rodapé para /br/jobs/... não ficam dentro de heading e são ignorados.
        page_jobs = 0
        for a in soup.select(
            'h1 a[href^="/br/jobs/"], h2 a[href^="/br/jobs/"], h3 a[href^="/br/jobs/"], h4 a[href^="/br/jobs/"]'
        ):
            href = a.get("href", "")
            if not href or href in seen_links:
                continue
            seen_links.add(href)
            page_jobs += 1

            card = _find_card(a)
            card_text = card.get_text(" ", strip=True)
            date_match = _DATE_RE.search(card_text)
            resumo = card.select_one("p")

            jobs.append(
                {
                    "id": f"bebee:{href}",
                    "fonte": "bebee",
                    "titulo": a.get_text(strip=True),
                    "empresa": _guess_company(card),
                    "cidade": _guess_location(card),
                    "estado": "",
                    "remoto": "remoto" in card_text.lower(),
                    "link": f"https://bebee.com{href}",
                    "descricao": strip_html(resumo.get_text(" ", strip=True) if resumo else card_text),
                    "publicado_em": date_match.group(1) if date_match else "",
                }
            )

        if page_jobs == 0:
            break

    return jobs


def fetch_bebee_descricao(link: str) -> str:
    """Descrição completa de uma vaga. O card da busca só traz ~300 caracteres;
    a página da vaga tem o texto inteiro no JSON-LD JobPosting (schema.org) —
    observado em 27/10/2026. Devolve "" se não achar."""
    resp = requests.get(link, headers=_HEADERS, timeout=20)
    if resp.status_code != 200:
        return ""
    soup = BeautifulSoup(resp.text, "html.parser")
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            dados = json.loads(script.string or "")
        except json.JSONDecodeError:
            continue
        for item in dados if isinstance(dados, list) else [dados]:
            if isinstance(item, dict) and item.get("@type") == "JobPosting":
                return strip_html(item.get("description", ""))
    return ""


def _find_card(a):
    # sobe a partir do link do título até o container do card; se a classe "p-4"
    # mudar, cai no primeiro ancestral que tenha um parágrafo de resumo.
    card = a.find_parent("div", class_="p-4")
    if card:
        return card
    node = a
    for _ in range(6):
        if node.parent is None:
            break
        node = node.parent
        if node.find("p"):
            return node
    return node


def _guess_company(card) -> str:
    # empresa com página própria vira link para /br/companies/<slug>; sem página,
    # é um <span class="truncate"> logo abaixo do local.
    el = card.select_one('a[href^="/br/companies/"]') or card.select_one("span.truncate")
    return el.get_text(strip=True) if el else ""


def _guess_location(card) -> str:
    # o local é o <span> que contém o ícone de alfinete de mapa.
    pin = card.select_one("svg.lucide-map-pin")
    span = pin.find_parent("span") if pin else None
    return span.get_text(strip=True) if span else ""

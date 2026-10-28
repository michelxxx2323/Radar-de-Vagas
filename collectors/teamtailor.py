"""
Coletor via feed RSS público do Teamtailor — ATS em que cada empresa tem um
site de carreiras próprio (às vezes num domínio da empresa, ex.
careers.jetshr.com.br da Jet Brasil) com um feed em <site>/jobs.rss.
Confirmado em 29/09/2026: o feed traz título, descrição completa (HTML), link,
data e modalidade de cada vaga, sem login.

Dá pra reconhecer um site Teamtailor pelos links /jobs.rss, /connect e
/cookie-policy na página de vagas.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

import requests

from ._util import registrar_board, strip_html, title_matches

_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/128.0.0.0 Safari/537.36"}
_TT_NS = "{https://teamtailor.com/locations}"


def fetch_teamtailor(site_url: str, empresa: str, term_filter="", limit: int | None = None) -> list[dict]:
    """site_url: raiz do site de carreiras (ex. https://careers.jetshr.com.br)."""
    resp = requests.get(site_url.rstrip("/") + "/jobs.rss", headers=_HEADERS, timeout=30)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    registrar_board("teamtailor", empresa, len(root.findall(".//item")))

    jobs = []
    for item in root.iter("item"):
        if limit is not None and len(jobs) >= limit:
            break
        titulo = (item.findtext("title") or "").strip()
        if not title_matches(titulo, term_filter):
            continue
        link = (item.findtext("link") or "").strip()
        remote_status = (item.findtext("remoteStatus") or "").lower()
        locais = item.find(f"{_TT_NS}locations")
        cidade = " ".join(" ".join(locais.itertext()).split()) if locais is not None else ""
        jobs.append(
            {
                "id": f"teamtailor:{link or titulo}",
                "fonte": "teamtailor",
                "titulo": titulo,
                "empresa": empresa,
                "cidade": cidade,
                "estado": "",
                "remoto": remote_status in ("fully", "remote"),
                "link": link,
                "descricao": strip_html(item.findtext("description") or ""),
                "publicado_em": _data(item.findtext("pubDate")),
            }
        )
    return jobs


def _data(pub: str | None) -> str:
    try:
        return parsedate_to_datetime(pub).date().isoformat() if pub else ""
    except (TypeError, ValueError):
        return ""

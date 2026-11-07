"""
Coletor do Personio, ATS/RH de muitas empresas pequenas e médias europeias.
Cada empresa publica um XML público, sem chave:

    GET https://<empresa>.jobs.personio.de/xml
        -> <workzag-jobs><position><id/><office/><name/><schedule/>
           <jobDescriptions><jobDescription><name/><value/></jobDescription>...
           </jobDescriptions><createdAt/></position>...</workzag-jobs>

O link da vaga é https://<empresa>.jobs.personio.de/job/<id>. Não há campo de
remoto: vale "remote" no escritório ou no título.

Criado em 07/11/2026 (fontes-vagas.md, seção 5). Config:
sources.personio.empresas: [{empresa: "Nome", id: "<subdomínio>"}].
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import requests

from ._util import registrar_board, strip_html, title_matches

PERSONIO_URL = "https://{id}.jobs.personio.de/xml"
UA = {"User-Agent": "Mozilla/5.0 (compatible; job-fit-screener/1.0)"}
_REMOTO_RE = re.compile(r"\b(remote|remoto|anywhere|home office|homeoffice)\b", re.IGNORECASE)


def fetch_personio(id_: str, empresa: str = "", term_filter=None, limit: int | None = None) -> list[dict]:
    resp = requests.get(PERSONIO_URL.format(id=id_), headers=UA, timeout=30)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    posicoes = root.findall(".//position")
    registrar_board("personio", empresa or id_, len(posicoes))
    jobs = []
    for p in posicoes:
        if limit is not None and len(jobs) >= limit:
            break
        titulo = (p.findtext("name") or "").strip()
        if not titulo or (term_filter is not None and not title_matches(titulo, term_filter)):
            continue
        escritorio = (p.findtext("office") or "").strip()
        partes = [f"{(d.findtext('name') or '').strip()}: {d.findtext('value') or ''}"
                  for d in p.findall(".//jobDescription")]
        vid = (p.findtext("id") or "").strip()
        jobs.append({
            "id": f"personio:{id_}:{vid}",
            "fonte": "personio",
            "titulo": titulo,
            "empresa": empresa or id_,
            "cidade": escritorio,
            "estado": "",
            "remoto": bool(_REMOTO_RE.search(f"{escritorio} {titulo}")),
            "link": f"https://{id_}.jobs.personio.de/job/{vid}" if vid else "",
            "descricao": strip_html(" ".join(partes)),
            "publicado_em": (p.findtext("createdAt") or "")[:10],
        })
    return jobs

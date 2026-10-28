"""
Coletor de vagas publicadas em páginas de carreira hospedadas na InHire
(<empresa>.inhire.app/vagas). A InHire é um ATS que cada empresa hospeda no
próprio subdomínio — não existe uma busca única entre empresas. Este
coletor varre a lista de empresas configurada em config.yaml
(sources.inhire.tenants), com filtro opcional por termo no título.

API não documentada oficialmente — descoberta inspecionando as chamadas de
rede da própria página pública de vagas (vr.inhire.app/vagas) em 21/09/2026.
Pode quebrar se a InHire mudar o contrato; se isso acontecer, repita a
inspeção (abra a página de vagas de qualquer empresa no InHire, com as
ferramentas de rede do navegador abertas, e procure chamadas para
api.inhire.app):

  GET https://api.inhire.app/job-posts/public/pages   [header X-Tenant: <slug>]
      -> {"jobsPage": [{"jobId","displayName","workplaceType","location","status"}, ...]}
  GET https://api.inhire.app/job-posts/public/pages/{jobId}  [header X-Tenant: <slug>]
      -> detalhes completos, incluindo "description"

<slug> é o subdomínio da empresa (ex.: "vr" para vr.inhire.app, "housi" para
housi.inhire.app) — confirmado com pelo menos duas empresas diferentes.
"""
from __future__ import annotations

import re
import unicodedata

import requests

from . import _cache
from ._util import registrar_board, strip_html, title_matches

INHIRE_LIST_URL = "https://api.inhire.app/job-posts/public/pages"
INHIRE_DETAIL_URL = "https://api.inhire.app/job-posts/public/pages/{job_id}"


def _slugify(titulo: str) -> str:
    """A SPA da InHire só renderiza os detalhes da vaga se a URL tiver um
    terceiro segmento depois do jobId (um slug do título) — confirmado ao
    vivo em 22/09/2026: `/vagas/<id>` sozinho fica em branco (só carrega a
    config do tenant, nunca chama o endpoint de detalhes), `/vagas/<id>/
    <qualquer-coisa>` carrega normalmente. O slug não precisa bater
    exatamente com o que o próprio site geraria (o roteamento usa só o
    jobId) — só precisa ser não-vazio e seguro pra URL.
    """
    normalizado = unicodedata.normalize("NFKD", titulo)
    sem_acento = normalizado.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", sem_acento).strip("-").lower()
    return slug or "vaga"


def _descricao(tenant: str, job_id: str) -> str:
    try:
        d = requests.get(INHIRE_DETAIL_URL.format(job_id=job_id), headers={"X-Tenant": tenant}, timeout=20)
        if d.status_code == 200:
            return strip_html(d.json().get("description", ""))
    except requests.RequestException:
        pass  # segue sem descrição — melhor uma vaga incompleta do que travar a coleta
    return ""


def fetch_inhire(
    tenant: str, term_filter="", fetch_descriptions: bool = True, limit: int | None = None
) -> list[dict]:
    """term_filter: trecho ou lista de trechos do título (ver title_matches).
    O filtro e o limit são aplicados ANTES de buscar a descrição de cada vaga,
    que é a parte lenta (uma requisição por vaga)."""
    resp = requests.get(INHIRE_LIST_URL, headers={"X-Tenant": tenant}, timeout=20)
    if resp.status_code != 200:
        registrar_board("inhire", tenant, 0, f"HTTP {resp.status_code}")
        return []
    payload = resp.json()
    raw_jobs = [j for j in payload.get("jobsPage", []) if j.get("status") == "published"]
    registrar_board("inhire", tenant, len(raw_jobs))

    jobs = []
    for j in raw_jobs:
        if limit is not None and len(jobs) >= limit:
            break
        titulo = j.get("displayName", "")
        if not title_matches(titulo, term_filter):
            continue

        job_id = j.get("jobId")
        descricao = ""
        if fetch_descriptions and job_id:
            descricao = _cache.descricao(f"inhire:{tenant}:{job_id}", lambda: _descricao(tenant, job_id))

        workplace = (j.get("workplaceType") or "").lower()
        jobs.append(
            {
                "id": f"inhire:{tenant}:{job_id}",
                "fonte": "inhire",
                "titulo": titulo,
                "empresa": tenant,
                "cidade": j.get("location", ""),
                "estado": "",
                "remoto": workplace == "remote",
                "link": f"https://{tenant}.inhire.app/vagas/{job_id}/{_slugify(titulo)}",
                "descricao": descricao,
                "publicado_em": "",
            }
        )
    return jobs

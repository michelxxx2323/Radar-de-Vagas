"""
Coletor do Jobicy (https://jobicy.com), board de vagas remotas com API pública
e gratuita, sem chave:

    GET https://jobicy.com/api/v2/remote-jobs?count=100&geo=<região>
        -> {"jobCount", "jobs": [{"id", "url", "jobTitle", "companyName",
            "jobGeo", "jobLevel", "jobDescription" (HTML), "pubDate",
            "annualSalaryMin", "annualSalaryMax", "salaryCurrency"}]}

O filtro `geo` da própria API separa o que aceita LATAM/Brasil/anywhere
(config_intl.yaml, sources.jobicy.geos). Mesmo assim cada vaga passa por
aceita_brasil, porque "jobGeo" às vezes é uma lista de países.

Criado em 07/11/2026 para o Perfil 2. Formato segundo a documentação do
Jobicy (jobicy.com/jobs-rss-feed) — conferir na 1ª rodada real. Termos de uso:
linkar a vaga no Jobicy e não chamar a API mais que algumas vezes por hora.
"""
from __future__ import annotations

import requests

from ._util import aceita_brasil, registrar_board, strip_html, title_matches

JOBICY_URL = "https://jobicy.com/api/v2/remote-jobs"
UA = {"User-Agent": "Mozilla/5.0 (compatible; job-fit-screener/1.0)", "Accept": "application/json"}


def _salario(j: dict) -> str:
    lo, hi, moeda = j.get("annualSalaryMin"), j.get("annualSalaryMax"), j.get("salaryCurrency") or "USD"
    if lo and hi:
        return f"{moeda} {lo} a {hi} /ano"
    return f"{moeda} {lo or hi} /ano" if (lo or hi) else ""


def fetch_jobicy(geo: str = "latam", term_filter=None, limit: int | None = None, count: int = 100) -> list[dict]:
    params = {"count": count}
    if geo and geo != "anywhere":
        params["geo"] = geo
    resp = requests.get(JOBICY_URL, params=params, headers=UA, timeout=30)
    resp.raise_for_status()
    brutas = resp.json().get("jobs") or []
    registrar_board("jobicy", geo or "todas", len(brutas))
    jobs = []
    for j in brutas:
        titulo = strip_html(j.get("jobTitle") or "")
        if not titulo or (term_filter is not None and not title_matches(titulo, term_filter)):
            continue
        local = (j.get("jobGeo") or "").strip()
        descricao = strip_html(j.get("jobDescription") or j.get("jobExcerpt") or "")
        if not aceita_brasil(local, descricao):
            continue
        jobs.append({
            "id": f"jobicy:{j.get('id')}",
            "fonte": "jobicy",
            "titulo": titulo,
            "empresa": (j.get("companyName") or "").strip(),
            "cidade": f"Remoto internacional — {local or 'local não informado'}",
            "estado": "",
            "remoto": True,
            "link": j.get("url") or "",
            "descricao": descricao,
            "publicado_em": str(j.get("pubDate") or "")[:10],
            "salario": _salario(j),
        })
    return jobs[:limit] if limit is not None else jobs

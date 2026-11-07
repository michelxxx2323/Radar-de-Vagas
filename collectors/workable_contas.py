"""
Coletor do Workable por empresa: o board de carreira de cada conta, pela
mesma API de widget que os sites de carreira usam (confirmada em 07/11/2026
com a Hire Overseas):

    GET https://apply.workable.com/api/v1/widget/accounts/<conta>?details=true
        -> {"name", "description", "jobs": [{"title", "shortcode", "code",
            "telecommuting", "url", "published_on", "country", "city", "state",
            "locations": [...], "description" (HTML), ...}]}

É o jeito certo de usar o Workable: a busca agregada do jobs.workable.com
(collectors/workable.py) não é feita para varredura e bloqueia (429) a rodada
inteira. Aqui é uma chamada por empresa da lista.

Cuidados:
  - a API devolve 200 mesmo para conta errada ou vazia ("hireoverseas" e
    "hire-overseas-1" existem com 0 vagas); só a contagem confirma a conta;
  - a mesma vaga vem uma vez por país (a Hire Overseas: 611 entradas, 109
    vagas): junta pelo shortcode, somando os países;
  - o link da página do jobs.workable.com/company/<id> não mostra a conta:
    teste o subdomínio no descobrir_ats.py.

Config: sources.workable_contas.empresas: [{empresa: "Nome", id: "<conta>"}].
"""
from __future__ import annotations

import requests

from ._util import aceita_brasil, registrar_board, strip_html, title_matches

WIDGET_URL = "https://apply.workable.com/api/v1/widget/accounts/{id}"
UA = {"User-Agent": "Mozilla/5.0 (compatible; job-fit-screener/1.0)", "Accept": "application/json"}


def _paises(j: dict) -> list[str]:
    nomes = [loc.get("country") for loc in (j.get("locations") or []) if isinstance(loc, dict)]
    return [n for n in [*nomes, j.get("country")] if n]


def agrupar(vagas: list[dict]) -> list[dict]:
    """Uma vaga por shortcode, com todos os países em que ela foi publicada."""
    por_codigo: dict[str, dict] = {}
    for j in vagas:
        chave = j.get("shortcode") or j.get("url") or j.get("title")
        if chave not in por_codigo:
            por_codigo[chave] = {**j, "_paises": []}
        atual = por_codigo[chave]
        for p in _paises(j):
            if p not in atual["_paises"]:
                atual["_paises"].append(p)
        atual["telecommuting"] = atual.get("telecommuting") or j.get("telecommuting")
    return list(por_codigo.values())


def fetch_workable_conta(id_: str, empresa: str = "", term_filter=None, limit: int | None = None) -> list[dict]:
    resp = requests.get(WIDGET_URL.format(id=id_), params={"details": "true"}, headers=UA, timeout=30)
    resp.raise_for_status()
    dados = resp.json() or {}
    vagas = agrupar(dados.get("jobs") or [])
    nome = empresa or dados.get("name") or id_
    registrar_board("workable_contas", nome, len(vagas))
    jobs = []
    for j in vagas:
        if limit is not None and len(jobs) >= limit:
            break
        titulo = (j.get("title") or "").strip()
        if not titulo or (term_filter is not None and not title_matches(titulo, term_filter)):
            continue
        remoto = bool(j.get("telecommuting"))
        paises = j["_paises"]
        descricao = strip_html(j.get("description") or "")
        local = ", ".join(paises)
        # remota: fica se um dos países for Brasil/LATAM ou a descrição aceitar;
        # presencial: o filtro de fora do Brasil do screen.py decide
        if remoto and not aceita_brasil(local or "Remote", descricao):
            continue
        cidade = f"Remoto — {local}" if remoto and local else (j.get("city") or local)
        link = j.get("url") or j.get("shortlink") or ""
        jobs.append({
            "id": f"workable_contas:{id_}:{j.get('shortcode') or link}",
            "fonte": "workable_contas",
            "titulo": titulo,
            "empresa": nome,
            "cidade": cidade,
            "estado": "" if remoto else (j.get("state") or ""),
            "remoto": remoto,
            "link": link,
            "descricao": descricao,
            "publicado_em": (j.get("published_on") or j.get("created_at") or "")[:10],
        })
    return jobs

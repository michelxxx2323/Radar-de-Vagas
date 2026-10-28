"""
Coletor de vagas da Gupy (ATS usado por muitas empresas no Brasil — BEES,
Wellhub e outras já aparecem no histórico deste projeto). Não requer chave.

Fonte principal (desde 24/09/2026): o MCP oficial da Gupy para candidatos
(https://candidatos.gupy.io/ia-para-pessoas-candidatas), ferramenta
`search_jobs`. Traz as mesmas vagas da API do portal (comparado ao vivo), mas
com um campo `salary` já interpretado pela Gupy — status (disclosed / range /
negotiable / variable / not_disclosed) e um rótulo em texto.

    POST https://candidates.mcp.api.gupy.io/mcp   (JSON-RPC 2.0, resposta em SSE)
    initialize -> notifications/initialized -> tools/call search_jobs

Fallback: se o MCP falhar, usa a API do portal (sem salário):

    GET https://employability-portal.gupy.io/api/v1/jobs?jobName=<termo>&offset=<n>&limit=<n>

IMPORTANTE: nenhuma das duas é API documentada oficialmente como contrato
estável — o parsing abaixo é defensivo (tenta vários nomes de campo).
"""
from __future__ import annotations

import json
import sys

import requests

from ._util import registrar_board, title_matches

GUPY_MCP_URL = "https://candidates.mcp.api.gupy.io/mcp"
# Endpoint antigo (portal.api.gupy.io/api/job) passou a dar 404 em 23/09/2026;
# este é o que o portal.gupy.io usa hoje — mesmos campos na resposta.
GUPY_URL = "https://employability-portal.gupy.io/api/v1/jobs"

_MCP_HEADERS = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
_mcp_session: dict | None = None  # headers da sessão MCP, reaproveitados entre termos


def fetch_gupy(term: str, limit: int = 15, city: str = "", debug_dump_path: str | None = None) -> list[dict]:
    try:
        raw_jobs = _search_mcp(term, limit, city)
    except Exception as e:  # noqa: BLE001 — qualquer falha do MCP cai pra API do portal
        print(f"  [aviso] MCP da Gupy falhou para '{term}' ({e}) — usando a API do portal, sem salário.",
              file=sys.stderr)
        raw_jobs = _search_portal(term, limit, city)

    if debug_dump_path and raw_jobs:
        with open(debug_dump_path, "w", encoding="utf-8") as f:
            json.dump(raw_jobs[0], f, ensure_ascii=False, indent=2)

    return [_to_job(j) for j in raw_jobs]


def fetch_gupy_empresa(career_page_name: str, term_filter="", limit: int | None = None) -> list[dict]:
    """Todas as vagas abertas de UMA empresa na Gupy (nome exato da career page,
    ex. "Americanas S.A." — veja em list_companies do MCP), filtradas pelo
    título. Útil pra empresas grandes cujas vagas não aparecem nos search_terms.
    Só funciona pelo MCP (a API do portal não filtra por empresa)."""
    raw = _paginate_mcp({"careerPageName": career_page_name}, max_items=2000)
    registrar_board("gupy", career_page_name, len(raw))
    jobs = [_to_job(j) for j in raw]
    jobs = [j for j in jobs if title_matches(j["titulo"], term_filter)]
    return jobs[:limit] if limit is not None else jobs


def _search_portal(term: str, limit: int, city: str) -> list[dict]:
    jobs: list[dict] = []
    while len(jobs) < limit:
        params = {"jobName": term, "offset": len(jobs), "limit": min(limit - len(jobs), 100)}
        if city:
            params["city"] = city
        resp = requests.get(GUPY_URL, params=params, timeout=20)
        resp.raise_for_status()
        payload = resp.json()
        page = payload.get("data") or payload.get("results") or payload.get("jobs") or []
        jobs += page
        if len(page) < params["limit"]:
            break
    return jobs


def _search_mcp(term: str, limit: int, city: str) -> list[dict]:
    args = {"term": term}
    if city:
        args["city"] = city
    return _paginate_mcp(args, max_items=limit)


def _paginate_mcp(args: dict, max_items: int) -> list[dict]:
    """search_jobs devolve no máximo 100 por chamada — pagina com offset."""
    jobs: list[dict] = []
    while len(jobs) < max_items:
        page_size = max(1, min(max_items - len(jobs), 100))
        result = _mcp_call("tools/call", {
            "name": "search_jobs", "arguments": {**args, "limit": page_size, "offset": len(jobs)},
        })
        if result.get("isError"):
            raise RuntimeError(str(result.get("content"))[:200])
        data = json.loads(result["content"][0]["text"])
        # a resposta vem aninhada: {"data": {"data": [...], "pagination": {...}}}
        while isinstance(data, dict) and "data" in data:
            data = data["data"]
        if not isinstance(data, list):
            raise RuntimeError(f"formato inesperado: {str(data)[:200]}")
        jobs += data
        if len(data) < page_size:
            break
    return jobs


def _mcp_call(method: str, params: dict) -> dict:
    global _mcp_session
    if _mcp_session is None:
        headers = dict(_MCP_HEADERS)
        resp = requests.post(GUPY_MCP_URL, headers=headers, timeout=30, json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "job-fit-screener", "version": "1.0"}},
        })
        resp.raise_for_status()
        if resp.headers.get("mcp-session-id"):
            headers["mcp-session-id"] = resp.headers["mcp-session-id"]
        requests.post(GUPY_MCP_URL, headers=headers, timeout=30,
                      json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        _mcp_session = headers

    resp = requests.post(GUPY_MCP_URL, headers=_mcp_session, timeout=60,
                         json={"jsonrpc": "2.0", "id": 2, "method": method, "params": params})
    if resp.status_code in (400, 404):
        _mcp_session = None  # sessão expirou — a próxima chamada abre outra
    resp.raise_for_status()
    msg = _parse_rpc(resp)
    if "error" in msg:
        raise RuntimeError(msg["error"])
    return msg["result"]


def _parse_rpc(resp) -> dict:
    # O servidor responde em SSE sem charset no content-type, e o requests
    # decodifica como latin-1 ("RemuneraÃ§Ã£o") — por isso decodifica os bytes
    # como UTF-8 à mão. Respostas grandes vêm quebradas em várias linhas
    # "data:" do mesmo evento, que precisam ser juntadas antes do json.loads.
    text = resp.content.decode("utf-8")
    partes = [line[5:].lstrip() for line in text.splitlines() if line.startswith("data:")]
    if partes:
        return json.loads("\n".join(partes))
    return json.loads(text)


def _to_job(j: dict) -> dict:
    titulo = j.get("name") or j.get("title") or j.get("jobName") or ""
    empresa = (
        j.get("careerPageName")
        or j.get("companyName")
        or (j.get("careerPage") or {}).get("name")
        or ""
    )
    cidade = j.get("city") or j.get("workplaceCity") or ""
    estado = j.get("state") or j.get("workplaceState") or ""
    remoto = bool(j.get("isRemoteWork") or j.get("remote") or j.get("workplaceType") == "remote")
    link = j.get("jobUrl") or j.get("url")
    if not link:
        job_id = j.get("id") or j.get("jobId")
        page = j.get("careerPageId") or j.get("careerPageName") or ""
        if job_id and page:
            link = f"https://{page}.gupy.io/job/{job_id}"
    salary = j.get("salary") or {}

    return {
        "id": f"gupy:{j.get('id') or j.get('jobId') or link}",
        "fonte": "gupy",
        "titulo": titulo,
        "empresa": empresa,
        "cidade": cidade,
        "estado": estado,
        "remoto": remoto,
        "link": link or "",
        "descricao": j.get("description") or j.get("jobDescription") or "",
        "publicado_em": j.get("publishedDate") or j.get("createdDate") or "",
        # só o MCP informa; "" quando veio da API do portal ou de outra fonte
        "salario": salary.get("label", "") if salary.get("status") != "not_disclosed" else "",
        "salario_tipo": salary.get("status", ""),
    }

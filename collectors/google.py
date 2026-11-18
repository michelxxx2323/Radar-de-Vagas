"""
Coletor das vagas do Google no Brasil (google.com/about/careers).

Pedido do Michel em 14/11/2026. A página de busca vem pronta do servidor, sem
precisar de JavaScript nem de login: as vagas ficam num bloco
`AF_initDataCallback({key: 'ds:1', ..., data: [...]})` dentro do HTML.

    GET https://www.google.com/about/careers/applications/jobs/results?location=Brazil&page=N
      -> data = [[vaga, vaga, ...], None, total, 20]

20 vagas por página; o total vem no próprio bloco (49 no Brasil em
14/11/2026, ou seja, 3 páginas). Cada vaga é uma lista posicional:

    [0] id        [1] título        [3][1] responsabilidades (HTML)
    [4][1] qualificações mínimas e desejáveis (HTML)    [7] empresa (Google, YouTube...)
    [9] locais: [rótulo, [endereço], cidade, None, UF, código do país]
    [10][1] "About the job" (HTML)    [12]/[13]/[14] criada/atualizada/publicada
    ([segundos, nanos])    [15][1] observações (entrevista presencial etc.)

A busca por país também traz vaga com vários países ("São Paulo, Mexico City,
Buenos Aires"), sempre com o Brasil entre eles. Vaga remota aparece com um local
só de país, sem cidade (("Brazil", None, None, "BR")) — é o que o filtro
has_remote=true do site devolve. A lista já traz a descrição completa, então
nenhuma requisição extra por vaga.

Se o Google mudar a página: abra a busca no navegador, "Exibir código-fonte" e
procure por "ds:1"; as posições acima são as que mudariam.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import unicodedata

import requests

from ._util import strip_html, title_matches

GOOGLE_URL = "https://www.google.com/about/careers/applications/jobs/results"
GOOGLE_JOB_URL = "https://www.google.com/about/careers/applications/jobs/results/{id}-{slug}"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}
_DS1_RE = re.compile(r"AF_initDataCallback\(\{key: 'ds:1'.*?data:(.*?), sideChannel: \{\}\}\);</script>", re.S)
_POR_PAGINA = 20
_MAX_PAGINAS = 15  # trava de segurança: 300 vagas (o Brasil tinha 49)


def ler_pagina(html: str) -> tuple[list, int]:
    """(vagas da página, total informado pelo site). ([], 0) se o bloco sumir."""
    m = _DS1_RE.search(html or "")
    if not m:
        return [], 0
    data = json.loads(m.group(1))
    vagas = data[0] if data and isinstance(data[0], list) else []
    total = data[2] if len(data) > 2 and isinstance(data[2], int) else len(vagas)
    return vagas, total


def _slug(titulo: str) -> str:
    t = unicodedata.normalize("NFKD", titulo).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")


def _html(campo) -> str:
    """Campos de texto vêm como [None, "<html>"]."""
    if isinstance(campo, list) and len(campo) > 1 and isinstance(campo[1], str):
        return campo[1]
    return ""


def _data(campo) -> str:
    if isinstance(campo, list) and campo and isinstance(campo[0], (int, float)):
        return dt.datetime.fromtimestamp(campo[0], tz=dt.timezone.utc).date().isoformat()
    return ""


def _pos(v: list, i: int):
    return v[i] if len(v) > i else None


def montar_vaga(v: list, pais: str = "BR") -> dict | None:
    """Vaga do Google no formato do radar, ou None se não tiver local no país."""
    locais = [loc for loc in (_pos(v, 9) or []) if isinstance(loc, list) and len(loc) > 5]
    no_pais = [loc for loc in locais if loc[5] == pais]
    if locais and not no_pais:
        return None
    # local só de país (sem cidade) = remoto no país, como no filtro has_remote do site
    remoto = any(not loc[2] and not loc[4] for loc in no_pais)
    cidades: list[str] = []
    for loc in no_pais:
        cidade = loc[2] or (str(loc[0]).split(",")[0].strip() if loc[4] else "")
        if cidade and cidade not in cidades:
            cidades.append(cidade)
    estados = sorted({loc[4] for loc in no_pais if loc[4]})
    titulo = (str(_pos(v, 1) or "")).strip()

    partes = []
    for rotulo, i in (("", 10), ("Responsabilidades: ", 3), ("", 4), ("", 15)):
        # título de seção (<h3>) ganha ":" pra não grudar no texto de baixo
        texto = strip_html(re.sub(r"(?<!:)\s*</h3>", ": </h3>", _html(_pos(v, i))))
        if texto:
            partes.append(rotulo + texto)
    outros = [str(loc[0]) for loc in locais if loc[5] != pais]
    if outros:
        partes.append("Também aberta em: " + "; ".join(outros) + ".")
    if remoto:
        partes.append("O site do Google marca esta vaga como remota no Brasil.")

    return {
        "id": f"google:{_pos(v, 0)}",
        "fonte": "google",
        "titulo": titulo,
        "empresa": str(_pos(v, 7) or "Google"),
        "cidade": " / ".join(cidades) if cidades else ("Remoto" if remoto else ""),
        "estado": "/".join(estados),
        "remoto": remoto,
        "link": GOOGLE_JOB_URL.format(id=_pos(v, 0), slug=_slug(titulo)),
        "descricao": "\n\n".join(partes),
        "publicado_em": _data(_pos(v, 14)) or _data(_pos(v, 12)),
    }


def fetch_google(location: str = "Brazil", term_filter="", limit: int | None = None,
                 pais: str = "BR") -> list[dict]:
    """Todas as vagas do Google no país (todas as páginas), filtradas pelo título.
    term_filter: trecho, lista de trechos ou função (ver title_matches)."""
    brutas: list = []
    vistos: set = set()
    pagina, total = 1, None
    while pagina <= _MAX_PAGINAS:
        params = {"location": location}
        if pagina > 1:
            params["page"] = pagina
        resp = requests.get(GOOGLE_URL, params=params, headers=_HEADERS, timeout=30)
        resp.raise_for_status()
        vagas, tot = ler_pagina(resp.text)
        if pagina == 1 and not vagas and "AF_initDataCallback" not in resp.text:
            # página veio sem o bloco de dados: o Google mudou o site ou bloqueou
            raise RuntimeError("página de vagas do Google sem o bloco de dados (ds:1)")
        total = tot if total is None else total
        novas = [v for v in vagas if v and v[0] not in vistos]
        vistos.update(v[0] for v in novas)
        brutas += novas
        if not novas or pagina * _POR_PAGINA >= (total or 0):
            break
        pagina += 1

    jobs = []
    for v in brutas:
        if limit is not None and len(jobs) >= limit:
            break
        if not title_matches(str(_pos(v, 1) or ""), term_filter):
            continue
        job = montar_vaga(v, pais=pais)
        if job:
            jobs.append(job)
    return jobs

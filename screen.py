#!/usr/bin/env python3
"""
Triagem de vagas contra o CV usando o Jev (TypeSafe AI).

Coleta vagas de fontes públicas (Gupy, Remotive), pede ao Jev uma nota de
aderência + recomendação para cada uma contra o seu currículo, e salva um
CSV ranqueado.

Uso:
    python screen.py                  # roda com a API real (precisa de TYPESAFE_API_KEY)
    python screen.py --mock           # roda offline, sem chave, para testar o pipeline
    python screen.py --config outro.yaml
    python screen.py --limit 5        # só 5 vagas por termo/fonte e por empresa, para um teste rápido
    python screen.py --no-cache       # repontua tudo, ignorando output/historico.json
    python screen.py --collect-only   # só coleta (output/vagas_coletadas.json), sem Jev
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import datetime as dt
import hashlib
import json
import re
import sys
import threading
from collections import Counter
from pathlib import Path

import requests
import yaml

from collectors.amazon import fetch_amazon
from collectors.ashby import fetch_ashby
from collectors.bebee import fetch_bebee
from collectors.didi import fetch_didi
from collectors.greenhouse import fetch_greenhouse
from collectors.gupy import fetch_gupy, fetch_gupy_empresa
from collectors.inhire import fetch_inhire
from collectors.lever import fetch_lever
from collectors.mercadolivre import fetch_mercadolivre
from collectors.remoteio import fetch_remoteio
from collectors.remoteok import fetch_remoteok
from collectors.remotive import fetch_remotive
from collectors.shopee import fetch_shopee
from collectors.smartrecruiters import fetch_smartrecruiters
from collectors.teamtailor import fetch_teamtailor
from collectors.tiktok import fetch_tiktok
from collectors.weworkremotely import fetch_weworkremotely
from collectors.workable import fetch_workable
from collectors.workday import fetch_workday
from collectors._util import _fold, title_filter, title_matches
from exportar_excel import write_xlsx
from jev_client import JevClient, JevError, MockJevClient

# Era 2500 até 23/09/2026 — cortava 95-100% das vagas da Greenhouse e da Ashby,
# justamente na parte dos requisitos (que costuma vir no fim).
MAX_DESCRICAO_CHARS = 8000

TRILHAS = {
    "revenue_operations": "Revenue/Sales Operations, BI, forecasting, funil, métricas SaaS (MRR/ARR/CAC/LTV)",
    "gtm_bd": "Go-to-Market, Business Development, parcerias estratégicas, originação de negócios",
    "business_analyst_estrategia": "Business analyst, estratégia corporativa, consultoria, análise de dados",
    "ecommerce_marketplace": "E-commerce, marketplace, growth D2C/DTC, marketing de performance",
    "produto_ops": "Product Operations, Product Strategy",
    "outro": "Não se encaixa claramente em nenhuma trilha acima",
}

RECOMENDACOES = {
    "aplicar_agora": "Vale montar CV e aplicar já — forte aderência, sem bloqueios óbvios",
    "avaliar": "Vale considerar, mas com ressalvas — ler a vaga com calma antes de aplicar",
    "baixa_prioridade": "Aderência fraca — só aplicar se faltarem opções melhores",
    "nao_aplicar": "Não vale o esforço de aplicar — desalinhado com o perfil",
}

RECOMENDACAO_ORDEM = {"aplicar_agora": 0, "avaliar": 1, "baixa_prioridade": 2, "nao_aplicar": 3}

# Regra do Michel (25/09/2026): analista/assistente JÚNIOR não bate com a
# senioridade dele, então no máximo "avaliar" (sem excluir). Cargos de gestão
# com "júnior" (Gestor/Coordenador/Gerente Júnior) ficam de fora de propósito —
# costumam ser senioridade maior que analista pleno/sênior. "Especialista" também
# nunca é rebaixada, seja qual for o nível depois (Júnior, I, II...) — pra ele
# especialista sempre é condizente com a senioridade dele.
_CARGO_OPERACIONAL_RE = re.compile(r"\b(analista|analyst|assistente|assistant|auxiliar)\b", re.IGNORECASE)
_JUNIOR_RE = re.compile(r"\b(j[uú]nior|jr)\b", re.IGNORECASE)

# Regra do Michel (15/10/2026): vaga paga em dólar não tem problema de
# senioridade — um júnior em USD ganha mais que um sênior no Brasil. Aqui só a
# detecção objetiva (fonte remota internacional, ou US$/USD no salário ou na
# descrição); o caso "empresa estrangeira sem salário explícito" fica pro Jev,
# via preferencias. "R$ 5.000" não conta como dólar.
_FONTES_DOLAR = {"remoteok", "remoteio", "remotive", "weworkremotely"}
_DOLAR_RE = re.compile(r"US\$|\bUSD\b|(?<![R$\w])\$\s?\d")


def paga_em_dolar(r: dict) -> bool:
    if r.get("fonte") in _FONTES_DOLAR:
        return True
    return bool(_DOLAR_RE.search(f"{r.get('salario') or ''} {r.get('descricao') or ''}"))


def ajustar_recomendacao(r: dict) -> dict:
    """Regras determinísticas aplicadas por cima da recomendação do Jev.
    Preenche `ajuste` com o motivo quando mexe na vaga."""
    r.setdefault("ajuste", "")
    titulo = r.get("titulo") or ""
    if (
        r.get("recomendacao") == "aplicar_agora"
        and _CARGO_OPERACIONAL_RE.search(titulo)
        and _JUNIOR_RE.search(titulo)
        and not paga_em_dolar(r)
    ):
        r["recomendacao"] = "avaliar"
        r["ajuste"] = "rebaixada de aplicar_agora: analista/assistente júnior"
    return r


def build_questions(com_preferencias: bool = False):
    questions = _base_questions()
    if com_preferencias:
        # `preferencias` (config.yaml) diz o que o candidato quer e NÃO quer fazer —
        # sem isso o Jev só compara CV x vaga e pode achar aderente uma vaga cuja
        # função central o candidato não exerce (ex.: vendas com meta).
        for q in questions.values():
            q["instructions"]["preferencias"] = "`preferencias`"
        questions["aderencia"]["instructions"]["question"] = (
            "Considerando o currículo em `cv` e as preferências do candidato em `preferencias`, "
            "quão aderente esse candidato é aos requisitos e ao escopo real da vaga `vaga`? "
            "Se a função central da vaga for algo que `preferencias` diz que o candidato não "
            "exerce, a aderência é baixa mesmo que haja pontos de contato no currículo."
        )
        questions["recomendacao"]["instructions"]["question"] = (
            "Dado o currículo em `cv`, as preferências do candidato em `preferencias` e a vaga "
            "`vaga`, qual a recomendação final sobre aplicar a essa vaga?"
        )
    return questions


def _base_questions():
    return {
        "aderencia": {
            "type": "score",
            "instructions": {
                "vaga": "`vaga`",
                "question": "Considerando o currículo em `cv`, quão aderente esse candidato é "
                "aos requisitos e ao escopo real da vaga `vaga`?",
            },
            "criteria": [
                "Sem aderência real — área de atuação totalmente diferente",
                "Aderência fraca — poucos pontos de contato com os requisitos centrais",
                "Aderência moderada — cobre parte dos requisitos centrais, com lacunas relevantes",
                "Aderência boa — cobre a maior parte dos requisitos centrais, lacunas menores",
                "Aderência excelente — cobre praticamente todos os requisitos centrais",
            ],
        },
        "senioridade_compativel": {
            "type": "noul",
            "instructions": {
                "vaga": "`vaga`",
                "question": "A senioridade exigida pela vaga `vaga` é compatível com a experiência "
                "de carreira relatada em `cv` (nem júnior demais, nem sênior demais para o candidato)?",
            },
            "criteria": {
                "true": "Nível de senioridade da vaga compatível com a experiência do candidato",
                "false": "Descompasso claro de senioridade (vaga muito júnior ou muito sênior)",
            },
        },
        "gap_critico": {
            "type": "noul",
            "instructions": {
                "vaga": "`vaga`",
                "question": "Existe algum requisito obrigatório na vaga `vaga` (ferramenta nomeada, "
                "certificação, idioma em nível específico, tempo mínimo em um setor, elegibilidade "
                "ou relocation) que claramente não é coberto pelo currículo em `cv`?",
            },
        },
        "trilha": {
            "type": "choice",
            "instructions": {
                "vaga": "`vaga`",
                "question": "Em qual trilha de atuação do candidato (descritas em `cv`) essa vaga "
                "`vaga` melhor se encaixa?",
            },
            "criteria": TRILHAS,
        },
        "recomendacao": {
            "type": "choice",
            "instructions": {
                "vaga": "`vaga`",
                "question": "Dado o currículo em `cv` e a vaga `vaga`, qual a recomendação final "
                "sobre aplicar a essa vaga?",
            },
            "criteria": RECOMENDACOES,
        },
    }


def normalize_key(job: dict) -> str:
    # sem acento/maiúsculas: a mesma vaga pode vir de fontes diferentes como
    # "Solfacil" e "Solfácil"
    return f"{_fold(job['empresa']).strip()}|{_fold(job['titulo']).strip()}"


def _add(jobs: list[dict], seen: set, found: list[dict]) -> None:
    for j in found:
        key = normalize_key(j)
        if key not in seen and j["titulo"]:
            seen.add(key)
            jobs.append(j)


def collect_jobs(config: dict) -> list[dict]:
    sources = config.get("sources", {})
    terms = config["search_terms"]
    limit = config.get("limit_per_term", 15)

    def on(nome: str) -> bool:
        return bool(sources.get(nome, {}).get("enabled"))

    # --- fontes por empresa / por página (sem busca por termo): trazem todas as
    # vagas abertas, então passam pelo filtro de título (title_keywords) e, em
    # testes com --limit, por um limite de vagas por empresa ---
    keywords = config.get("title_keywords") or []
    company_limit = config.get("limit_per_company")  # None = sem limite
    # title_exclude: vagas de loja, logística, engenharia etc. que batem com
    # alguma keyword ("vendas", "operações") mas nunca servem — descartadas antes
    # do Jev (e, na InHire/Shopee, antes até de buscar a descrição)
    excludes = config.get("title_exclude") or []
    titulo_ok = title_filter(keywords, excludes)

    def _filtered(found: list[dict]) -> list[dict]:
        found = [j for j in found if titulo_ok(j["titulo"])]
        return found[:company_limit] if company_limit is not None else found

    # Cada busca (um termo numa fonte, ou uma empresa) vira uma tarefa; as
    # tarefas rodam em paralelo (collect_workers) e os resultados entram na
    # ordem em que as tarefas foram criadas — a mesma ordem da coleta
    # sequencial de antes, então a deduplicação não muda.
    tarefas: list[tuple] = []  # (fonte, rótulo do aviso, função)

    def tarefa(fonte: str, rotulo: str, fn) -> None:
        tarefas.append((fonte, rotulo, fn))

    # --- fontes com busca por palavra-chave: uma chamada por termo ---
    # a Gupy tem limite próprio (limit_per_term dentro de sources.gupy): com
    # o geral de 15, "e-commerce" trazia 15 de 213 vagas. Em teste com
    # --limit, vale o limite do teste.
    gupy_limit = (
        limit if config.get("limit_per_company") is not None
        else sources.get("gupy", {}).get("limit_per_term", limit)
    )
    for term in terms:
        if on("gupy"):
            tarefa("gupy", f"Gupy falhou para '{term}'",
                   lambda t=term: fetch_gupy(t, limit=gupy_limit, city=sources["gupy"].get("city", "")))
        if on("remotive"):
            tarefa("remotive", f"Remotive falhou para '{term}'",
                   lambda t=term: fetch_remotive(t, category=sources["remotive"].get("category", ""), limit=limit))
        if on("weworkremotely"):
            tarefa("weworkremotely", f"We Work Remotely falhou para '{term}'",
                   lambda t=term: fetch_weworkremotely(
                       t, category=sources["weworkremotely"].get("category", ""), limit=limit))
        if on("bebee"):
            tarefa("bebee", f"beBee falhou para '{term}'",
                   lambda t=term: fetch_bebee(t, max_pages=sources["bebee"].get("max_pages_per_term", 2)))
        if on("amazon"):
            tarefa("amazon", f"Amazon falhou para '{term}'",
                   lambda t=term: fetch_amazon(t, limit=limit, country=sources["amazon"].get("country", "BRA")))

    # Mercado Livre: sem busca por termo (ver collectors/mercadolivre.py),
    # traz as N primeiras páginas de vagas do Brasil uma única vez
    if on("mercadolivre"):
        tarefa("mercadolivre", "Mercado Livre falhou",
               lambda: _filtered(fetch_mercadolivre(max_pages=sources["mercadolivre"].get("max_pages", 2))))

    if on("greenhouse"):
        for board in sources["greenhouse"].get("boards", []):
            tarefa("greenhouse", f"Greenhouse falhou para '{board}'",
                   lambda b=board: _filtered(fetch_greenhouse(b, term_filter=titulo_ok)))

    if on("inhire"):
        fetch_desc = sources["inhire"].get("fetch_descriptions", True)
        for tenant in sources["inhire"].get("tenants", []):
            # filtro e limite dentro do coletor, pra não buscar descrição de vaga descartada
            tarefa("inhire", f"InHire falhou para '{tenant}'",
                   lambda t=tenant: fetch_inhire(
                       t, term_filter=titulo_ok, fetch_descriptions=fetch_desc, limit=company_limit))

    if on("lever"):
        for board in sources["lever"].get("boards", []):
            # item simples ("loadsmart") ou {board, country} pra boards globais
            if isinstance(board, dict):
                board, country = board["board"], board.get("country", "")
            else:
                country = ""
            tarefa("lever", f"Lever falhou para '{board}'",
                   lambda b=board, c=country: _filtered(fetch_lever(b, country=c)))

    if on("ashby"):
        for board in sources["ashby"].get("boards", []):
            tarefa("ashby", f"Ashby falhou para '{board}'", lambda b=board: _filtered(fetch_ashby(b)))

    # Gupy por empresa: todas as vagas de empresas grandes (Renner, Hering...)
    # que não apareceriam só pelos search_terms
    if on("gupy"):
        for empresa in sources["gupy"].get("empresas", []):
            tarefa("gupy", f"Gupy falhou para a empresa '{empresa}'",
                   lambda e=empresa: fetch_gupy_empresa(e, term_filter=titulo_ok, limit=company_limit))

    if on("shopee"):
        # filtro e limite dentro do coletor, pra não buscar descrição de vaga descartada
        tarefa("shopee", "Shopee falhou", lambda: fetch_shopee(term_filter=titulo_ok, limit=company_limit))

    if on("tiktok"):
        tarefa("tiktok", "TikTok falhou", lambda: fetch_tiktok(term_filter=titulo_ok, limit=company_limit))

    # 99 (DiDi): site de vagas próprio da DiDi, filtrado pelo país
    if on("didi"):
        tarefa("didi", "DiDi/99 falhou", lambda: fetch_didi(
            country=sources["didi"].get("country", "Brazil"), term_filter=titulo_ok, limit=company_limit))

    if on("workday"):
        for site in sources["workday"].get("sites", []):
            tarefa("workday", f"Workday falhou para '{site.get('empresa')}'",
                   lambda s=site: fetch_workday(s["url"], s["empresa"], term_filter=titulo_ok, limit=company_limit))

    if on("smartrecruiters"):
        for emp in sources["smartrecruiters"].get("empresas", []):
            tarefa("smartrecruiters", f"SmartRecruiters falhou para '{emp.get('empresa')}'",
                   lambda e=emp: fetch_smartrecruiters(
                       e["id"], e.get("empresa", ""), term_filter=titulo_ok, limit=company_limit))

    if on("teamtailor"):
        for site in sources["teamtailor"].get("sites", []):
            tarefa("teamtailor", f"Teamtailor falhou para '{site.get('empresa')}'",
                   lambda s=site: fetch_teamtailor(s["url"], s["empresa"], term_filter=titulo_ok, limit=company_limit))

    # Workable: busca por termo no portal que junta todas as empresas que usam o
    # Workable; a busca casa o termo também na descrição, então passa pelo
    # filtro de título como as fontes por empresa
    if on("workable"):
        wk = sources["workable"]
        wk_limit = limit if company_limit is not None else wk.get("limit_per_term", 60)
        for term in terms:
            tarefa("workable", f"Workable falhou para '{term}'",
                   lambda t=term: fetch_workable(
                       t, location=wk.get("location", "Brazil"), limit=wk_limit, term_filter=titulo_ok))

    # vagas remotas de empresas internacionais que aceitam Brasil/LATAM
    if on("remoteok"):
        tarefa("remoteok", "Remote OK falhou", lambda: fetch_remoteok(
            tags=sources["remoteok"].get("tags"), term_filter=titulo_ok, limit=company_limit))

    if on("remoteio"):
        tarefa("remoteio", "Remote.io falhou", lambda: fetch_remoteio(
            locais=sources["remoteio"].get("locais"), areas=sources["remoteio"].get("areas"),
            term_filter=titulo_ok, limit=company_limit))

    resultados = _rodar_tarefas(tarefas, config)

    jobs: list[dict] = []
    seen: set = set()
    for found in resultados:
        _add(jobs, seen, found)

    # a exclusão vale também pras fontes de busca por termo (a Gupy com
    # "e-commerce" traz vendedor, caixa, estágio...)
    if excludes:
        jobs = [j for j in jobs if not title_matches(j["titulo"], excludes)]
    return jobs


# Máximo de buscas simultâneas no MESMO site, pra não parecer ataque nem
# levar bloqueio (o total geral é collect_workers). beBee é leitura de HTML e
# já bloqueou acesso automatizado antes — vai mais devagar.
_SIMULTANEAS_POR_FONTE = {"bebee": 2}
_SIMULTANEAS_PADRAO = 4


def _rodar_tarefas(tarefas: list, config: dict) -> list:
    """Roda as tarefas de coleta em paralelo e devolve os resultados na ordem
    das tarefas (tarefa que falhou devolve [] e imprime o aviso)."""
    limites = {
        fonte: threading.BoundedSemaphore(_SIMULTANEAS_POR_FONTE.get(fonte, _SIMULTANEAS_PADRAO))
        for fonte, _, _ in tarefas
    }

    def _rodar(fonte: str, rotulo: str, fn) -> list:
        with limites[fonte]:
            try:
                return fn() or []
            except Exception as e:  # noqa: BLE001
                print(f"  [aviso] {rotulo}: {e}", file=sys.stderr)
                return []

    resultados: list = [[] for _ in tarefas]
    total = len(tarefas)
    if not total:
        return resultados
    feitas, proximo_aviso = 0, 0.1
    workers = max(1, config.get("collect_workers", 16))
    print(f"  {total} buscas em {len(limites)} fontes, até {workers} ao mesmo tempo...", flush=True)
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futuros = {ex.submit(_rodar, *t): i for i, t in enumerate(tarefas)}
        for fut in cf.as_completed(futuros):
            resultados[futuros[fut]] = fut.result()
            feitas += 1
            if feitas / total >= proximo_aviso or feitas == total:
                print(f"  coleta: {feitas}/{total} buscas concluídas", flush=True)
                proximo_aviso += 0.1
    return resultados


def score_job(client, cv_text: str, job: dict, questions: dict, preferencias: str = "") -> dict:
    state = {
        "cv": cv_text,
        "vaga": {
            "titulo": job["titulo"],
            "empresa": job["empresa"],
            "local": f"{job['cidade']} {job['estado']}".strip() or ("Remoto" if job["remoto"] else ""),
            "remoto": job["remoto"],
            "descricao": (job["descricao"] or "")[:MAX_DESCRICAO_CHARS],
        },
    }
    if preferencias:
        state["preferencias"] = preferencias
    resp = client.evaluate(state, questions)
    answers = resp["answers"]

    aderencia = answers["aderencia"]
    senioridade = answers["senioridade_compativel"]["noul"]
    gap = answers["gap_critico"]["noul"]
    trilha = answers["trilha"]["choice"]
    recomendacao = answers["recomendacao"]["choice"]

    n_levels = len(aderencia["legend"])
    fit_normalizado = aderencia["score"] / max(n_levels - 1, 1)  # 0..1
    composite = (
        fit_normalizado * 10 * 0.55
        + senioridade * 10 * 0.20
        + (1 - gap) * 10 * 0.15
        + aderencia["confidence"] * 10 * 0.10
    )

    return {
        **job,
        "nota_fit": round(composite, 1),
        "aderencia_nivel": aderencia["legend"][str(int(aderencia["score"]))],
        "confianca": aderencia["confidence"],
        "senioridade_ok": "sim" if senioridade >= 0.5 else "não",
        "gap_critico": "sim" if gap >= 0.5 else "não",
        "trilha": trilha,
        "recomendacao": recomendacao,
    }


# Campos gerados pelo Jev que ficam salvos no histórico — o resto (link, cidade,
# publicado_em...) sempre vem da coleta atual.
CACHE_SCORE_FIELDS = [
    "nota_fit",
    "aderencia_nivel",
    "confianca",
    "senioridade_ok",
    "gap_critico",
    "trilha",
    "recomendacao",
]


def load_cache(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        # históricos salvos antes de 23/09/2026 têm chaves com acento; normaliza
        # pro formato atual de normalize_key pra não repontuar essas vagas.
        return {_fold(k): v for k, v in raw.items()}
    except (json.JSONDecodeError, OSError) as e:
        print(f"  [aviso] histórico ilegível em {path} ({e}) — começando do zero.", file=sys.stderr)
        return {}


def save_cache(path: Path, cache: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")


PREFERENCIAS_LOCAIS = Path("preferencias.txt")


def carregar_preferencias(config: dict) -> str:
    """Preferências do candidato: o arquivo local `preferencias.txt` (fora do
    git, pra não publicar dados pessoais) tem prioridade sobre o exemplo que
    está no config.yaml."""
    if PREFERENCIAS_LOCAIS.exists():
        return PREFERENCIAS_LOCAIS.read_text(encoding="utf-8").strip()
    return (config.get("preferencias") or "").strip()


def scoring_version(cv_text: str, preferencias: str, questions: dict) -> str:
    """Impressão digital de tudo que influencia a nota. Muda quando o CV, as
    preferências, as perguntas ou o limite de texto mudam — e aí as notas
    antigas do histórico deixam de ser reaproveitadas, sem precisar de --no-cache."""
    blob = json.dumps(
        [cv_text, preferencias, questions, MAX_DESCRICAO_CHARS], ensure_ascii=False, sort_keys=True
    )
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]


def score_jobs_with_cache(
    client,
    cv_text: str,
    jobs: list[dict],
    questions: dict,
    cache: dict,
    checkpoint=None,
    preferencias: str = "",
    versao: str | None = None,
    workers: int = 1,
):
    """Pontua só as vagas que ainda não estão no histórico; reaproveita a nota das
    demais. O novo histórico contém apenas as vagas desta coleta, então vagas
    encerradas saem sozinhas.

    `versao` (ver scoring_version), se passada, é gravada em cada entrada; uma
    entrada de outra versão é repontuada.

    `checkpoint(cache_parcial)`, se passado, é chamado a cada 25 vagas novas com
    o histórico antigo + o que já foi pontuado, pra uma queda no meio da rodada
    não jogar fora o trabalho feito.

    `workers`: quantas vagas vão pro Jev ao mesmo tempo (config `jev_workers`).

    Retorna (results, novo_cache, n_reaproveitadas, n_pontuadas)."""
    def _entrada(r: dict) -> dict:
        e = {f: r[f] for f in CACHE_SCORE_FIELDS}
        if versao is not None:
            e["_versao"] = versao
        if r.get("_desc_completa"):
            e["_desc_completa"] = True
        if r.get("adicionada_em"):
            e["_adicionada_em"] = r["adicionada_em"]
        return e

    # 1) separa o que dá pra reaproveitar do que precisa ir pro Jev
    por_indice: dict[int, dict] = {}
    new_cache: dict = {}
    pendentes: list[tuple[int, str, dict]] = []
    for idx, job in enumerate(jobs):
        key = normalize_key(job)
        cached = cache.get(key)
        if (
            cached
            and all(f in cached for f in CACHE_SCORE_FIELDS)
            and (versao is None or cached.get("_versao") == versao)
            # nota dada com o resumo da vaga, e agora há a descrição completa: repontua
            and not (job.get("_desc_completa") and not cached.get("_desc_completa"))
        ):
            por_indice[idx] = {**job, **{f: cached[f] for f in CACHE_SCORE_FIELDS},
                               "_desc_completa": job.get("_desc_completa") or cached.get("_desc_completa")}
            new_cache[key] = _entrada(por_indice[idx])
        else:
            pendentes.append((idx, key, job))
    reaproveitadas = len(por_indice)

    # 2) pontua as pendentes em paralelo (`workers` chamadas simultâneas ao
    # Jev). Resultados e histórico são tratados só aqui, na thread principal.
    pontuadas = falhas = 0
    if pendentes:
        print(f"  {len(pendentes)} vagas pro Jev, {workers} por vez...", flush=True)
    with cf.ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futuros = {
            ex.submit(score_job, client, cv_text, job, questions, preferencias): (idx, key, job)
            for idx, key, job in pendentes
        }
        for fut in cf.as_completed(futuros):
            idx, key, job = futuros[fut]
            try:
                r = fut.result()
            except JevError as e:
                falhas += 1
                print(f"  [aviso] falha ao pontuar '{job['titulo']}' ({job['empresa']}): {e}", file=sys.stderr)
                continue
            por_indice[idx] = r
            new_cache[key] = _entrada(r)
            pontuadas += 1
            if pontuadas % 10 == 0:
                print(f"  {pontuadas} novas pontuadas (de {len(pendentes)})", flush=True)
            if checkpoint and pontuadas % 25 == 0:
                checkpoint({**cache, **new_cache})
    if falhas:
        print(f"  [aviso] {falhas} vagas não pontuadas (ficam pra próxima rodada).", file=sys.stderr)

    results = [por_indice[k] for k in sorted(por_indice)]
    return results, new_cache, reaproveitadas, pontuadas


def completar_descricoes_bebee(jobs: list[dict], cache: dict, versao: str, workers: int = 2) -> int:
    """A busca do beBee só traz ~300 caracteres de cada vaga — e o Jev dava nota
    alta sem ver requisitos como "SQL avançado" ou "100% presencial" (caso
    real de 27/10/2026: 9,3 que caiu para 6,8). Busca a página completa das vagas do beBee que ainda
    não têm nota dada com a descrição inteira; as que já têm reaproveitam a
    nota sem baixar nada. Marca as completadas com `_desc_completa`, o que faz
    as notas antigas (do resumo) serem refeitas uma vez. `workers` segue o
    limite do beBee em _SIMULTANEAS_POR_FONTE. Retorna quantas completou."""
    from collectors.bebee import fetch_bebee_descricao

    def precisa(job: dict) -> bool:
        cached = cache.get(normalize_key(job)) or {}
        return job["fonte"] == "bebee" and not (cached.get("_desc_completa") and cached.get("_versao") == versao)

    def completar(job: dict) -> bool:
        try:
            texto = fetch_bebee_descricao(job["link"])
        except requests.RequestException:
            return False
        if len(texto) <= len(job.get("descricao") or ""):
            return False
        job["descricao"] = texto
        job["_desc_completa"] = True
        return True

    pendentes = [j for j in jobs if precisa(j)]
    if not pendentes:
        return 0
    print(f"  buscando a descrição completa de {len(pendentes)} vagas do beBee...", flush=True)
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        completadas = sum(ex.map(completar, pendentes))
    if completadas < len(pendentes):
        print(f"  [aviso] {len(pendentes) - completadas} vagas do beBee ficaram só com o resumo.", file=sys.stderr)
    return completadas


def datar_vagas(jobs: list[dict], historico: dict, hoje: str) -> None:
    """Preenche `adicionada_em`: o dia em que a vaga apareceu pela 1ª vez (o
    painel marca como "nova" as do dia do relatório). A data fica no histórico
    em `_adicionada_em`; vaga que não está no histórico é de hoje. As que já
    estavam lá antes de 28/10/2026, quando a data passou a ser guardada, ficam
    sem data."""
    for job in jobs:
        anterior = historico.get(normalize_key(job))
        job["adicionada_em"] = anterior.get("_adicionada_em", "") if anterior is not None else hoje


def _apagar_relatorios_antigos(out_dir: Path) -> None:
    """Mantém só o relatório de hoje (vagas_triadas_*.csv/.xlsx e novas_*.csv) —
    pedido do Michel em 26/10/2026, pra não acumular uma planilha por dia.
    O histórico de notas fica no historico.json, então nada se perde."""
    hoje = dt.date.today().isoformat()
    apagados = 0
    for padrao in ("vagas_triadas_*.csv", "vagas_triadas_*.xlsx", "novas_*.csv"):
        for p in out_dir.glob(padrao):
            if hoje in p.name:
                continue
            try:
                p.unlink()
                apagados += 1
            except OSError:
                pass  # aberto no Excel: fica pra próxima rodada
    if apagados:
        print(f"  {apagados} relatórios de dias anteriores apagados (fica só o de hoje).")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--mock", action="store_true", help="usa o cliente simulado, sem rede/chave")
    ap.add_argument(
        "--limit",
        type=int,
        help="máximo de vagas por termo/fonte E por empresa (Greenhouse, InHire...), para testes rápidos",
    )
    ap.add_argument(
        "--no-cache",
        action="store_true",
        help="ignora o histórico e repontua todas as vagas (mudanças no CV/preferências já "
        "fazem isso sozinhas)",
    )
    ap.add_argument(
        "--collect-only",
        action="store_true",
        help="só coleta e salva output/vagas_coletadas.json, sem chamar o Jev",
    )
    args = ap.parse_args()

    config = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    if args.limit:
        config["limit_per_term"] = args.limit
        config["limit_per_company"] = args.limit

    cv_path = Path(config["cv_path"])
    if not cv_path.exists():
        sys.exit(
            f"Arquivo de CV não encontrado: {cv_path}. Copie cv.exemplo.txt para {cv_path} "
            "e cole o texto do seu currículo (o cv.txt fica fora do git)."
        )
    cv_text = cv_path.read_text(encoding="utf-8")

    if args.mock:
        client = MockJevClient(model=config.get("jev_model", "jev-mock"))
        print("Rodando em modo --mock (sem chamadas reais ao Jev).")
    else:
        try:
            client = JevClient(model=config.get("jev_model", "jev-latest"))
        except JevError as e:
            sys.exit(f"{e}")

    print("Coletando vagas...")
    jobs = collect_jobs(config)
    print(f"  {len(jobs)} vagas únicas coletadas.")
    por_fonte = Counter(j["fonte"] for j in jobs)
    print("  por fonte: " + ", ".join(f"{f} {n}" for f, n in por_fonte.most_common()))
    # fonte ligada que veio vazia quase sempre é site que mudou/bloqueou, não
    # falta de vaga — sem este aviso, as vagas dela sumiam do relatório em silêncio
    for fonte, cfg in config.get("sources", {}).items():
        if cfg.get("enabled") and not por_fonte.get(fonte):
            print(f"  [aviso] a fonte '{fonte}' está ligada mas não trouxe nenhuma vaga — "
                  f"provavelmente o site mudou ou bloqueou o acesso (ver collectors/{fonte}.py).",
                  file=sys.stderr)

    if not jobs:
        print("Nenhuma vaga encontrada — confira os termos de busca e as fontes no config.yaml.")
        return

    out_dir = Path(config.get("output_dir", "output"))
    out_dir.mkdir(exist_ok=True)

    # O histórico do modo --mock fica separado, pra notas simuladas nunca
    # contaminarem as reais.
    cache_path = out_dir / ("historico_mock.json" if args.mock else "historico.json")
    # com --no-cache as notas são ignoradas, mas a data em que cada vaga
    # apareceu continua valendo
    historico = load_cache(cache_path)
    cache = {} if args.no_cache else historico
    datar_vagas(jobs, historico, dt.date.today().isoformat())

    preferencias = carregar_preferencias(config)
    questions = build_questions(com_preferencias=bool(preferencias))
    versao = scoring_version(cv_text, preferencias, questions)

    if config.get("sources", {}).get("bebee", {}).get("enabled"):
        completar_descricoes_bebee(jobs, cache, versao, workers=_SIMULTANEAS_POR_FONTE["bebee"])

    # vagas completas (com descrição) desta coleta — usadas pelo calibrar.py e pelo kit.py
    coletadas_path = out_dir / "vagas_coletadas.json"
    coletadas_path.write_text(json.dumps(jobs, ensure_ascii=False), encoding="utf-8")
    if args.collect_only:
        print(f"Vagas salvas em {coletadas_path} (--collect-only: sem pontuação).")
        return

    print(f"Pontuando com o Jev ({len(cache)} vagas no histórico, versão {versao})...")
    results, new_cache, reaproveitadas, pontuadas = score_jobs_with_cache(
        client,
        cv_text,
        jobs,
        questions,
        cache,
        checkpoint=lambda c: save_cache(cache_path, c),
        preferencias=preferencias,
        versao=versao,
        workers=config.get("jev_workers", 1),
    )
    save_cache(cache_path, new_cache)
    print(f"  {pontuadas} vagas novas pontuadas, {reaproveitadas} reaproveitadas do histórico.")

    min_score = config.get("min_score", 0)
    results = [ajustar_recomendacao(r) for r in results if r["nota_fit"] >= min_score]
    results.sort(key=lambda r: (RECOMENDACAO_ORDEM.get(r["recomendacao"], 9), -r["nota_fit"]))

    # Vagas que nunca tinham aparecido em nenhuma rodada (não estavam no
    # histórico antes desta). Com histórico vazio ou --no-cache tudo pareceria
    # "novo", então aí não avisa.
    if cache and not args.no_cache and not args.mock:
        from novidades import salvar_e_avisar
        novas = [r for r in results if normalize_key(r) not in cache]
        salvar_e_avisar(novas, out_dir, config)

    out_path = out_dir / f"vagas_triadas_{dt.date.today().isoformat()}.csv"

    columns = [
        "recomendacao",
        "nota_fit",
        "aderencia_nivel",
        "confianca",
        "senioridade_ok",
        "gap_critico",
        "trilha",
        "titulo",
        "empresa",
        "cidade",
        "estado",
        "remoto",
        "salario",
        "fonte",
        "publicado_em",
        "adicionada_em",
        "link",
        "ajuste",
    ]
    with out_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for r in results:
            writer.writerow({c: r.get(c, "") for c in columns})

    xlsx_path = out_path.with_suffix(".xlsx")
    try:
        write_xlsx(results, xlsx_path)
    except PermissionError:
        # acontece quando a planilha anterior do mesmo dia está aberta no Excel
        print(f"  [aviso] não consegui salvar {xlsx_path} — feche o arquivo no Excel e rode "
              f"`python exportar_excel.py`.", file=sys.stderr)

    print(f"\nRelatório salvo em: {out_path} (e {xlsx_path.name}, com links clicáveis)")
    _apagar_relatorios_antigos(out_dir)
    print("\nTop 10:")
    for r in results[:10]:
        print(f"  [{r['recomendacao']:15s}] {r['nota_fit']:4.1f}  {r['titulo']} — {r['empresa']} ({r['trilha']})")


if __name__ == "__main__":
    main()

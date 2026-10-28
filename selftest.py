#!/usr/bin/env python3
"""
Teste offline do pipeline — não faz nenhuma chamada de rede.

Verifica duas coisas:
1. O parsing dos coletores (gupy/remotive) contra um JSON de exemplo no
   formato que cada API costuma devolver (útil para checar rapidamente se o
   formato mudou, sem precisar de rede).
2. O fluxo completo pontuação -> ranking -> CSV, usando o MockJevClient e
   vagas fabricadas (sem rede, sem chave).

Rode com: python selftest.py
"""
from __future__ import annotations

import csv
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))

from collectors.amazon import AMAZON_SEARCH_URL, fetch_amazon  # noqa: E402
from collectors.ashby import ASHBY_URL, fetch_ashby  # noqa: E402
from collectors.bebee import fetch_bebee, fetch_bebee_descricao  # noqa: E402
from collectors.greenhouse import GREENHOUSE_URL, fetch_greenhouse  # noqa: E402
from collectors.gupy import GUPY_URL, fetch_gupy  # noqa: E402
from collectors.inhire import INHIRE_DETAIL_URL, INHIRE_LIST_URL, fetch_inhire  # noqa: E402
from collectors.lever import LEVER_URL, fetch_lever  # noqa: E402
from collectors.mercadolivre import fetch_mercadolivre  # noqa: E402
from collectors.remotive import REMOTIVE_URL, fetch_remotive  # noqa: E402
from collectors.shopee import SHOPEE_SEARCH_URL, fetch_shopee  # noqa: E402
from collectors.smartrecruiters import fetch_smartrecruiters  # noqa: E402
from collectors.teamtailor import fetch_teamtailor  # noqa: E402
from collectors.workday import fetch_workday, parse_site  # noqa: E402
from collectors.tiktok import fetch_tiktok  # noqa: E402
from collectors.weworkremotely import fetch_weworkremotely  # noqa: E402
from collectors._util import strip_html, title_filter, title_matches  # noqa: E402
from jev_client import MockJevClient  # noqa: E402
from screen import (  # noqa: E402
    CACHE_SCORE_FIELDS,
    ajustar_recomendacao,
    build_questions,
    completar_descricoes_bebee,
    load_cache,
    normalize_key,
    save_cache,
    score_job,
    score_jobs_with_cache,
    scoring_version,
)


class _FakeResp:
    def __init__(self, payload, status_code: int = 200, text: str | None = None, content: bytes | None = None):
        self._payload = payload
        self.status_code = status_code
        self.text = text if text is not None else ""
        self.content = content if content is not None else self.text.encode()

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def test_gupy_parsing():
    fake_payload = {
        "data": [
            {
                "id": 123,
                "name": "Analista de Novos Negócios Pleno",
                "careerPageName": "wellhub",
                "city": "São Paulo",
                "state": "SP",
                "isRemoteWork": False,
                "description": "Vaga de BD e parcerias, foco em novos negócios.",
                "publishedDate": "2026-09-10",
            }
        ]
    }
    # MCP fora do ar -> cai na API do portal (sem salário)
    import collectors.gupy as gupy_mod
    gupy_mod._mcp_session = None
    with patch("collectors.gupy.requests.post", side_effect=RuntimeError("MCP indisponível")), \
         patch("collectors.gupy.requests.get", return_value=_FakeResp(fake_payload)) as m:
        jobs = fetch_gupy("novos negocios", limit=5)
    assert m.call_args.args[0] == GUPY_URL
    assert len(jobs) == 1
    j = jobs[0]
    assert j["titulo"] == "Analista de Novos Negócios Pleno"
    assert j["empresa"] == "wellhub"
    assert j["cidade"] == "São Paulo"
    assert j["remoto"] is False
    assert j["fonte"] == "gupy"
    assert j["salario"] == ""

    # MCP ok -> mesma vaga, agora com salário. Formato copiado da resposta real
    # de search_jobs em 24/09/2026 (SSE, JSON aninhado em data.data).
    mcp_job = dict(fake_payload["data"][0], jobUrl="https://wellhub.gupy.io/job/123",
                   salary={"status": "range", "label": "R$ 8.000 a R$ 10.000", "confidence": "high"})
    tool_text = json.dumps({"data": {"data": [mcp_job], "pagination": {"total": 1}}})
    sse = "event: message\ndata: " + json.dumps(
        {"jsonrpc": "2.0", "id": 2, "result": {"content": [{"type": "text", "text": tool_text}]}}
    )
    init = "event: message\ndata: " + json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}})

    class _Resp(_FakeResp):
        headers = {"mcp-session-id": "abc"}

    def fake_post(url, headers=None, timeout=None, json=None):
        assert url == gupy_mod.GUPY_MCP_URL
        if json["method"] == "tools/call":
            assert json["params"]["name"] == "search_jobs"
            assert json["params"]["arguments"]["term"] == "novos negocios"
            assert headers["mcp-session-id"] == "abc"
            return _Resp(None, text=sse)
        return _Resp(None, text=init)

    gupy_mod._mcp_session = None
    with patch("collectors.gupy.requests.post", side_effect=fake_post), \
         patch("collectors.gupy.requests.get", side_effect=AssertionError("não devia usar o portal")):
        jobs = fetch_gupy("novos negocios", limit=5)
    gupy_mod._mcp_session = None
    assert len(jobs) == 1
    assert jobs[0]["salario"] == "R$ 8.000 a R$ 10.000"
    assert jobs[0]["salario_tipo"] == "range"
    assert jobs[0]["link"] == "https://wellhub.gupy.io/job/123"
    print("OK  test_gupy_parsing")


def test_remotive_parsing():
    fake_payload = {
        "jobs": [
            {
                "id": 456,
                "title": "Business Development Manager",
                "company_name": "Acme Corp",
                "candidate_required_location": "Worldwide",
                "url": "https://remotive.com/job/456",
                "description": "<p>Own the <b>BD</b> pipeline.</p>",
                "publication_date": "2026-09-15",
            }
        ]
    }
    with patch("collectors.remotive.requests.get", return_value=_FakeResp(fake_payload)) as m:
        jobs = fetch_remotive("business development", limit=5)
    assert m.call_args.args[0] == REMOTIVE_URL
    assert len(jobs) == 1
    j = jobs[0]
    assert j["titulo"] == "Business Development Manager"
    assert j["remoto"] is True
    assert "<" not in j["descricao"]  # HTML foi removido
    print("OK  test_remotive_parsing")


def test_greenhouse_parsing():
    # Formato confirmado na documentação oficial (docs.greenhouse.io/job-board.html)
    # em 21/09/2026, com content=true.
    fake_payload = {
        "jobs": [
            {
                "id": 127817,
                "title": "Analista de Estratégia e Novos Negócios",
                "updated_at": "2026-09-10T10:55:28-05:00",
                "location": {"name": "São Paulo, Brazil"},
                "absolute_url": "https://boards.greenhouse.io/acme/jobs/127817",
                "content": "<p>Vaga de <b>estratégia</b> e BD.</p>",
            },
            {
                "id": 127818,
                "title": "Backend Engineer",
                "updated_at": "2026-09-11T10:55:28-05:00",
                "location": {"name": "Remote"},
                "absolute_url": "https://boards.greenhouse.io/acme/jobs/127818",
                "content": "<p>Engenharia backend.</p>",
            },
        ]
    }
    with patch("collectors.greenhouse.requests.get", return_value=_FakeResp(fake_payload)) as m:
        jobs = fetch_greenhouse("acme")
    assert m.call_args.args[0] == GREENHOUSE_URL.format(board="acme")
    assert len(jobs) == 2
    assert jobs[0]["empresa"] == "acme"
    assert jobs[0]["remoto"] is False
    assert jobs[1]["remoto"] is True  # "Remote" na location

    # filtro por termo no título
    with patch("collectors.greenhouse.requests.get", return_value=_FakeResp(fake_payload)):
        filtrados = fetch_greenhouse("acme", term_filter="estrat")
    assert len(filtrados) == 1
    assert filtrados[0]["titulo"] == "Analista de Estratégia e Novos Negócios"

    # board inexistente -> 404 -> lista vazia, sem exceção
    with patch("collectors.greenhouse.requests.get", return_value=_FakeResp({}, status_code=404)):
        assert fetch_greenhouse("empresa-que-nao-usa-greenhouse") == []

    print("OK  test_greenhouse_parsing")


def test_inhire_parsing():
    # Formato confirmado inspecionando a rede de vr.inhire.app/vagas em 21/09/2026.
    list_payload = {
        "jobsPage": [
            {
                "jobId": "f2695c3d-af3d-49b1-9150-30cd7655d6f7",
                "displayName": "Analista de Novos Negócios Pleno",
                "status": "published",
                "workplaceType": "Hybrid",
                "location": "São Paulo, SP, BR",
            },
            {
                "jobId": "167fdf8a-b812-400c-af46-96da959b5108",
                "displayName": "Vaga em Rascunho",
                "status": "draft",  # não deve entrar (só "published")
                "workplaceType": "Remote",
                "location": "BR",
            },
        ]
    }
    detail_payload = {"description": "<p>Descrição completa da vaga.</p>"}

    def fake_get(url, headers=None, timeout=None):
        if url == INHIRE_LIST_URL:
            assert headers.get("X-Tenant") == "vr"
            return _FakeResp(list_payload)
        assert url == INHIRE_DETAIL_URL.format(job_id="f2695c3d-af3d-49b1-9150-30cd7655d6f7")
        return _FakeResp(detail_payload)

    with patch("collectors.inhire.requests.get", side_effect=fake_get):
        jobs = fetch_inhire("vr")

    assert len(jobs) == 1  # a vaga "draft" foi filtrada
    assert jobs[0]["empresa"] == "vr"
    assert jobs[0]["remoto"] is False  # "Hybrid" != "remote"
    # link precisa de um slug depois do jobId, senão a SPA abre em branco (ver collectors/inhire.py)
    assert jobs[0]["link"].startswith("https://vr.inhire.app/vagas/f2695c3d-af3d-49b1-9150-30cd7655d6f7/")
    assert len(jobs[0]["link"].rsplit("/", 1)[-1]) > 0
    assert "Descrição completa" in jobs[0]["descricao"]

    # tenant sem vagas / endpoint fora do ar -> lista vazia, sem exceção
    with patch("collectors.inhire.requests.get", return_value=_FakeResp({}, status_code=503)):
        assert fetch_inhire("tenant-invalido") == []

    print("OK  test_inhire_parsing")


def test_lever_parsing():
    # Formato confirmado inspecionando a API real em 21/09/2026
    # (api.lever.co/v0/postings/ciandt?mode=json).
    fake_payload = [
        {
            "id": "abc-123",
            "text": "Business Development Analyst",
            "categories": {"location": "Brazil", "team": "Growth", "commitment": "Homeoffice"},
            "country": "BR",
            "workplaceType": "remote",
            "descriptionPlain": "Vaga de BD, remota.",
            "createdAt": 1758470400000,  # 2025-09-21 em ms
            "hostedUrl": "https://jobs.lever.co/acme/abc-123",
        }
    ]
    with patch("collectors.lever.requests.get", return_value=_FakeResp(fake_payload)) as m:
        jobs = fetch_lever("acme")
    assert m.call_args.args[0] == LEVER_URL.format(company="acme")
    assert len(jobs) == 1
    j = jobs[0]
    assert j["titulo"] == "Business Development Analyst"
    assert j["empresa"] == "acme"
    assert j["cidade"] == "Brazil"
    assert j["remoto"] is True
    assert j["fonte"] == "lever"
    assert j["link"] == "https://jobs.lever.co/acme/abc-123"

    # board inexistente -> 404 -> lista vazia, sem exceção
    with patch("collectors.lever.requests.get", return_value=_FakeResp([], status_code=404)):
        assert fetch_lever("empresa-que-nao-usa-lever") == []

    print("OK  test_lever_parsing")


def test_ashby_parsing():
    # Formato confirmado inspecionando a API real em 21/09/2026
    # (api.ashbyhq.com/posting-api/job-board/nubank).
    fake_payload = {
        "jobs": [
            {
                "id": "xyz-789",
                "title": "Revenue Operations Analyst",
                "location": "São Paulo",
                "isRemote": False,
                "workplaceType": "Hybrid",
                "publishedAt": "2026-09-18T12:00:00.000+00:00",
                "jobUrl": "https://jobs.ashbyhq.com/acme/xyz-789",
                "descriptionPlain": "Vaga de RevOps, híbrida em SP.",
            }
        ]
    }
    with patch("collectors.ashby.requests.get", return_value=_FakeResp(fake_payload)) as m:
        jobs = fetch_ashby("acme")
    assert m.call_args.args[0] == ASHBY_URL.format(company="acme")
    assert len(jobs) == 1
    j = jobs[0]
    assert j["titulo"] == "Revenue Operations Analyst"
    assert j["empresa"] == "acme"
    assert j["cidade"] == "São Paulo"
    assert j["remoto"] is False
    assert j["publicado_em"] == "2026-09-18"
    assert j["fonte"] == "ashby"

    # board inexistente -> 404 -> lista vazia, sem exceção
    with patch("collectors.ashby.requests.get", return_value=_FakeResp({}, status_code=404)):
        assert fetch_ashby("empresa-que-nao-usa-ashby") == []

    print("OK  test_ashby_parsing")


def test_bebee_parsing():
    # Estrutura (simplificada) copiada do HTML real de bebee.com/br/jobs?q=growth
    # em 23/09/2026 — cada card é um <div class="p-4"> com o título em <h3><a>.
    fake_html = """
    <html><body>
    <nav><a href="/br/jobs/remote">Vagas remotas</a></nav>
    <div class="p-4">
      <div class="flex items-start gap-2 mb-1.5"><div class="flex-1 min-w-0">
        <div class="flex items-center gap-2 mb-0.5">
          <h3 class="font-semibold"><a href="/br/jobs/analista-bd-acme-sp--abc123">Analista de Business Development</a></h3>
        </div>
        <span class="flex items-center gap-1 text-sm"><svg class="lucide lucide-map-pin h-3.5 w-3.5"></svg>São Paulo</span>
      </div></div>
      <div><span class="truncate">Acme Ltda</span></div>
      <p class="text-sm line-clamp-2 mb-2">Principais atividades: prospecção e negociação B2B</p>
      <span class="rounded-full">Tempo inteiro</span>
      <span class="rounded-full">Híbrido</span>
      <div class="text-[11px]">Hoje</div>
    </div>
    </body></html>
    """
    with patch("collectors.bebee.requests.get", return_value=_FakeResp(None, text=fake_html)) as m:
        jobs = fetch_bebee("business development", max_pages=1)
    assert m.call_args.kwargs["params"]["q"] == "business development"
    assert len(jobs) == 1  # o link de navegação (fora de heading) foi ignorado
    j = jobs[0]
    assert j["titulo"] == "Analista de Business Development"
    assert j["empresa"] == "Acme Ltda"
    assert j["cidade"] == "São Paulo"
    assert j["publicado_em"] == "Hoje"
    assert j["descricao"] == "Principais atividades: prospecção e negociação B2B"
    assert j["remoto"] is False
    print("OK  test_bebee_parsing")

    # a página da vaga traz a descrição inteira no JSON-LD (formato de 27/10/2026)
    pagina = """<html><head>
    <script type="application/ld+json">[{"@type": "Organization", "name": "beBee"}]</script>
    <script type="application/ld+json">{"@type": "JobPosting", "title": "Analista",
      "description": "<h4><strong>Sobre a vaga</strong></h4>\\nTexto completo &amp; requisitos"}</script>
    </head><body></body></html>"""
    with patch("collectors.bebee.requests.get", return_value=_FakeResp(None, text=pagina)):
        assert fetch_bebee_descricao("https://bebee.com/br/jobs/x") == "Sobre a vaga Texto completo & requisitos"
    with patch("collectors.bebee.requests.get", return_value=_FakeResp(None, text="<html></html>")):
        assert fetch_bebee_descricao("https://bebee.com/br/jobs/x") == ""
    print("OK  test_bebee_descricao")


def test_amazon_parsing():
    # Formato confirmado inspecionando amazon.jobs/en/search.json ao vivo em
    # 21/09/2026 (endpoint não documentado oficialmente, mas usado pelo
    # próprio front-end, sem autenticação).
    fake_payload = {
        "jobs": [
            {
                "id": "244934e2-6d0d-408e-88b8-80ff0a7ddfa5",
                "id_icims": "10501105",
                "title": "Senior Business Development - Connect (LATAM)",
                "company_name": "Amazon AWS Services Brazil Ltd - E07",
                "city": "Sao Paulo",
                "state": "SP",
                "location": "BR, SP, Sao Paulo",
                "normalized_location": "Sao Paulo, Sao Paulo, BRA",
                "job_path": "/en/jobs/10501105/senior-business-development-connect-latam",
                "description": "<p>Vaga de <b>Business Development</b> para LATAM.</p>",
                "description_short": "Business Development para LATAM.",
                "posted_date": "August 13, 2026",
            }
        ]
    }
    with patch("collectors.amazon.requests.get", return_value=_FakeResp(fake_payload)) as m:
        jobs = fetch_amazon("business development", limit=5)
    assert m.call_args.args[0] == AMAZON_SEARCH_URL
    assert m.call_args.kwargs["params"]["base_query"] == "business development"
    assert len(jobs) == 1
    j = jobs[0]
    assert j["id"] == "amazon:10501105"
    assert j["titulo"] == "Senior Business Development - Connect (LATAM)"
    assert j["cidade"] == "Sao Paulo"
    assert j["link"].endswith("/en/jobs/10501105/senior-business-development-connect-latam")
    assert "<" not in j["descricao"]  # HTML foi removido

    # falha de rede/bloqueio -> lista vazia, sem exceção
    with patch("collectors.amazon.requests.get", return_value=_FakeResp({}, status_code=503)):
        assert fetch_amazon("business development") == []

    print("OK  test_amazon_parsing")


def test_mercadolivre_parsing():
    # HTML ilustrativo baseado na árvore real inspecionada via navegador em
    # careers-meli.mercadolibre.com/en/positions em 21/09/2026 (classes
    # styled-components tipo "sc-331a73cc-N" — instáveis entre builds, daqui
    # o parser usa <h3>/<img> como âncoras em vez de depender delas).
    fake_html = """
    <html><body><main>
    <div class="sc-331a73cc-0">
      <div>
        <h3>Expert de Desenvolvimento de Negócios - Marketplace</h3>
        <div>
          <div aria-hidden="true"><span>GhostCategoria</span><span>GhostTipo</span></div>
          <div><span>Marketplace</span><span>On-site</span></div>
          <span><img src="/icons/location.svg" alt=""><span>Nordeste, Brazil</span></span>
        </div>
      </div>
      <div>
        <div>
          <span>Published 8 hours</span>
          <a href="/en/positions?id=111">Learn more</a>
        </div>
      </div>
    </div>
    <div class="sc-331a73cc-0">
      <div>
        <h3>Data Engineer - Financial Risk</h3>
        <div>
          <div aria-hidden="true"><span>GhostCategoria</span></div>
          <div><span>Technology</span><span>On-site</span></div>
          <span><img src="/icons/location.svg" alt=""><span>Sudeste, Brazil</span></span>
        </div>
      </div>
      <div>
        <div>
          <span>Published 4 hours</span>
          <a href="/en/positions?id=222">Learn more</a>
        </div>
      </div>
    </div>
    </main></body></html>
    """
    with patch("collectors.mercadolivre.requests.get", return_value=_FakeResp(None, text=fake_html)) as m:
        jobs = fetch_mercadolivre(max_pages=1)
    assert m.call_args.kwargs["params"]["country"] == "Brazil"
    assert len(jobs) == 2
    j = jobs[0]
    assert j["id"] == "mercadolivre:111"
    assert j["titulo"] == "Expert de Desenvolvimento de Negócios - Marketplace"
    assert j["cidade"] == "Nordeste, Brazil"
    assert j["publicado_em"] == "Published 8 hours"
    assert j["link"] == "https://careers-meli.mercadolibre.com/en/positions?id=111"

    # bloqueio antibot (403) -> lista vazia, sem exceção
    with patch("collectors.mercadolivre.requests.get", return_value=_FakeResp(None, status_code=403, text="")):
        assert fetch_mercadolivre(max_pages=1) == []

    print("OK  test_mercadolivre_parsing")


def test_weworkremotely_parsing():
    # Formato confirmado lendo o RSS real de weworkremotely.com em 21/09/2026.
    fake_rss = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0"><channel>
      <item>
        <title>Acme Corp: Business Development Manager</title>
        <region>Anywhere in the World</region>
        <category>Sales and Marketing</category>
        <type>Full-Time</type>
        <description>&lt;p&gt;Vaga de &lt;b&gt;BD&lt;/b&gt; remota.&lt;/p&gt;</description>
        <pubDate>Mon, 21 Sep 2026 18:54:53 +0000</pubDate>
        <link>https://weworkremotely.com/remote-jobs/acme-bd-manager</link>
      </item>
      <item>
        <title>Outra Empresa: Backend Engineer</title>
        <region>Anywhere in the World</region>
        <category>Programming</category>
        <type>Full-Time</type>
        <description>Vaga de engenharia.</description>
        <pubDate>Mon, 21 Sep 2026 07:30:54 +0000</pubDate>
        <link>https://weworkremotely.com/remote-jobs/outra-backend</link>
      </item>
    </channel></rss>"""
    with patch("collectors.weworkremotely.requests.get", return_value=_FakeResp(None, content=fake_rss.encode())):
        jobs = fetch_weworkremotely("business development", category="remote-sales-and-marketing-jobs")
    assert len(jobs) == 1
    j = jobs[0]
    assert j["empresa"] == "Acme Corp"
    assert j["titulo"] == "Business Development Manager"
    assert j["remoto"] is True
    assert "BD remota" in j["descricao"]
    print("OK  test_weworkremotely_parsing")


def test_aceita_brasil():
    from collectors._util import aceita_brasil
    aceitas = ["Worldwide", "Brazil", "Northern America, LATAM, Europe", "Americas, Europe, Israel",
               "Anywhere in the World", "Latin America"]
    recusadas = ["USA", "Remote - US", "Europe", "Anywhere in the United States",
                 "USA, Canada, Argentina, Mexico, Peru", "United Kingdom"]
    for loc in aceitas:
        assert aceita_brasil(loc), loc
    for loc in recusadas:
        assert not aceita_brasil(loc), loc
    # local vazio/"Remote" (e "Anywhere" do Remote.io): decide pela descrição
    assert not aceita_brasil("", "Great team in NYC.")
    assert aceita_brasil("Remote", "We hire across Latin America.")
    assert not aceita_brasil("Anywhere", "Philadelphia territory.", anywhere_confiavel=False)
    assert aceita_brasil("Anywhere", "Open to candidates in Brazil.", anywhere_confiavel=False)
    print("OK  test_aceita_brasil")


def test_remoteok_remoteio_parsing():
    from collectors import remoteio
    from collectors.remoteok import fetch_remoteok
    # formato real da API do Remote OK em 29/09/2026: 1º item é o aviso legal
    api = [
        {"legal": "API Terms of Service..."},
        {"id": "1", "position": "Revenue Operations Manager", "company": "Acme", "location": "LATAM",
         "description": "<p>Own RevOps.</p>", "url": "https://remoteOK.com/remote-jobs/1",
         "date": "2026-09-29T10:00:00+00:00", "salary_min": 60000, "salary_max": 80000},
        {"id": "2", "position": "Sales Operations Analyst", "company": "US Co", "location": "Remote - US",
         "description": "US only", "url": "https://remoteOK.com/remote-jobs/2", "date": ""},
    ]
    with patch("collectors.remoteok.requests.get", return_value=_FakeResp(api)),             patch("collectors.remoteok.time.sleep"):
        jobs = fetch_remoteok(tags=[], term_filter=["operations"])
    assert [j["empresa"] for j in jobs] == ["Acme"]
    assert jobs[0]["link"] == "https://remoteOK.com/remote-jobs/1"  # termos de uso: linkar o Remote OK
    assert jobs[0]["salario"] == "US$ 60,000 a US$ 80,000 /ano"

    # Remote.io: card da listagem + JSON-LD da página da vaga (formato de 29/09/2026)
    listagem = """<article><div><a href="/remote-jobs/operations/bizops-lead-at-acme-123" style="x"><h3 style="y">BizOps Lead</h3></a>
<p style="z">Acme</p>
<div style="w">
<span>Argentina, Brazil</span><span>Operations</span></div></div></article>
<article><div><a href="/remote-jobs/operations/ops-at-usco-456" style="x"><h3 style="y">Operations Manager</h3></a>
<p style="z">US Co</p>
<div style="w">
<span>United States</span><span>Operations</span></div></div></article>"""
    detalhe = """<script type="application/ld+json">{"@type":"JobPosting","title":"BizOps Lead",
"description":"<p>Run business operations.</p>","datePosted":"2026-09-29"}</script>"""
    paginas = {"https://www.remote.io/remote-jobs/brazil/operations": listagem,
               "https://www.remote.io/remote-jobs/operations/bizops-lead-at-acme-123": detalhe}
    with patch("collectors.remoteio._get", side_effect=lambda u: paginas[u]):
        jobs = remoteio.fetch_remoteio(locais=["brazil"], areas=["operations"], term_filter=["lead", "operations"])
    assert [j["titulo"] for j in jobs] == ["BizOps Lead"]
    assert jobs[0]["descricao"] == "Run business operations."
    assert jobs[0]["id"] == "remoteio:123"
    print("OK  test_remoteok_remoteio_parsing")


def test_didi_parsing():
    from collectors import didi
    # formato real da API da DiDi em 30/09/2026 (lista + detalhe)
    lista = {"success": True, "result": [
        {"id": "21493", "jobTitle": "Commercial Planning Senior Analyst", "address": "Sao Paulo - Brazil"},
        {"id": "9281", "jobTitle": "Backend Engineer", "address": "Sao Paulo - Brazil"},
    ]}
    detalhe = {"success": True, "result": {"hire": "435 - 99 FOOD LTDA", "teamRoleDetail": "<p>Time de planejamento.</p>",
                                           "roleDetail": "<ol><li>Definir metas.</li></ol>", "eagerDetail": "None"}}
    with patch("collectors.didi.requests.post", return_value=_FakeResp(lista)),             patch("collectors.didi.requests.get", return_value=_FakeResp(detalhe)),             patch("collectors.didi._ca_bundle", return_value=True):
        jobs = didi.fetch_didi(term_filter=["planning"])
    assert len(jobs) == 1
    j = jobs[0]
    assert j["empresa"] == "99 FOOD LTDA" and j["cidade"] == "Sao Paulo"
    assert j["link"] == "https://careers.didiglobal.com/jobDetail:21493"
    assert j["descricao"] == "Time de planejamento. Definir metas."
    print("OK  test_didi_parsing")


def test_workable_parsing():
    from collectors import workable
    # formato real da API de jobs.workable.com em 15/10/2026 (2 páginas)
    pag1 = {"totalSize": 3, "nextPageToken": "tok2", "jobs": [
        {"id": "a1", "title": "Revenue Operations Manager", "state": "published", "workplace": "remote",
         "description": "<p>Own the <b>RevOps</b> stack.</p>", "requirementsSection": "<ul><li>5+ years</li></ul>",
         "benefitsSection": "", "url": "https://jobs.workable.com/view/a1", "created": "2026-10-10T12:00:00Z",
         "location": {"city": "", "subregion": None, "countryName": "Brazil"}, "company": {"title": "Acme"}},
        {"id": "a2", "title": "Backend Engineer", "state": "published", "workplace": "on_site",
         "description": "x", "url": "https://jobs.workable.com/view/a2", "location": {}, "company": {"title": "Acme"}},
    ]}
    pag2 = {"totalSize": 3, "jobs": [
        {"id": "a3", "title": "Marketplace Operations Lead", "state": "published", "workplace": "hybrid",
         "description": "<p>Sellers.</p>", "url": "https://jobs.workable.com/view/a3", "created": "2026-10-01T00:00:00Z",
         "location": {"city": "São Paulo", "subregion": "State of São Paulo", "countryName": "Brazil"},
         "company": {"title": "Beta"}},
    ]}
    with patch("collectors.workable.requests.get", side_effect=[_FakeResp(pag1), _FakeResp(pag2)]) as m,             patch("collectors.workable.time.sleep"):
        jobs = workable.fetch_workable("operations", term_filter=["operations"])
    assert m.call_args_list[1].kwargs["params"]["pageToken"] == "tok2"  # paginou pelo token
    assert [j["titulo"] for j in jobs] == ["Revenue Operations Manager", "Marketplace Operations Lead"]
    j = jobs[0]
    assert j["empresa"] == "Acme" and j["remoto"] is True and j["cidade"] == "Brazil"
    assert j["descricao"] == "Own the RevOps stack. 5+ years" and j["publicado_em"] == "2026-10-10"
    assert jobs[1]["cidade"] == "São Paulo" and jobs[1]["remoto"] is False
    print("OK  test_workable_parsing")


def test_title_filter():
    kw = ["negocio", "go to market", "estrateg"]
    assert title_matches("Analista de Novos Negócios Pleno", kw)  # acento ignorado
    assert title_matches("Gerente de GO TO MARKET", kw)  # maiúsculas ignoradas
    assert title_matches("Especialista em Estratégia", kw)
    assert not title_matches("Engenheiro de Software Backend", kw)
    assert title_matches("Qualquer coisa", [])  # sem keywords -> tudo passa
    assert title_matches("Qualquer coisa", "")

    # exclusão: bate com keyword mas é vaga de loja -> fora
    ok = title_filter(["vendas", "e-commerce"], ["supervisor de vendas", "loja ", "de loja"])
    assert not ok("Supervisor de Vendas - Renner - Jundiaí/SP")
    assert not ok("Hering Loja I Assessor de Vendas")
    assert ok("|LOJAS RENNER| Analista de E-commerce")  # "lojas" não é "loja "
    assert ok("Analista de Planejamento de Vendas")
    assert title_filter([], [])("qualquer")

    # InHire: filtro e limit aplicados antes de buscar a descrição de cada vaga
    list_payload = {
        "jobsPage": [
            {"jobId": "a", "displayName": "Analista de Novos Negócios", "status": "published"},
            {"jobId": "b", "displayName": "Engenheiro Backend", "status": "published"},
            {"jobId": "c", "displayName": "Especialista de Estratégia", "status": "published"},
        ]
    }
    detalhes_buscados = []

    def fake_get(url, headers=None, timeout=None):
        if url == INHIRE_LIST_URL:
            return _FakeResp(list_payload)
        detalhes_buscados.append(url)
        return _FakeResp({"description": "x"})

    with patch("collectors.inhire.requests.get", side_effect=fake_get):
        jobs = fetch_inhire("vr", term_filter=kw)
    assert [j["titulo"] for j in jobs] == ["Analista de Novos Negócios", "Especialista de Estratégia"]
    assert len(detalhes_buscados) == 2  # a vaga de engenharia nem teve a descrição buscada

    detalhes_buscados.clear()
    with patch("collectors.inhire.requests.get", side_effect=fake_get):
        jobs = fetch_inhire("vr", term_filter=kw, limit=1)
    assert len(jobs) == 1 and len(detalhes_buscados) == 1

    print("OK  test_title_filter")


def test_descricao_completa():
    # Greenhouse manda HTML escapado — antes chegava ao Jev como "&lt;p&gt;..."
    assert strip_html("&lt;p&gt;&lt;strong&gt;Sobre&lt;/strong&gt; a vaga &amp;amp; mais&lt;/p&gt;") == "Sobre a vaga & mais"
    assert strip_html("<p>normal</p>") == "normal"

    # Amazon: requisitos vêm em campos separados e têm que entrar na descrição
    payload = {"jobs": [{
        "id_icims": "1", "title": "Vendor Manager", "company_name": "Amazon",
        "description": "<p>Gerir vendors.</p>",
        "basic_qualifications": "- Experience with P&amp;L ownership",
        "preferred_qualifications": "- Vendor negotiations Our inclusive culture empowers Amazonians...",
    }]}
    with patch("collectors.amazon.requests.get", return_value=_FakeResp(payload)):
        d = fetch_amazon("vendor", limit=1)[0]["descricao"]
    assert "Gerir vendors." in d
    assert "Requisitos obrigatórios: - Experience with P&L ownership" in d
    assert "Requisitos desejáveis: - Vendor negotiations" in d
    assert "inclusive culture" not in d  # texto institucional cortado

    # Lever: requisitos ficam em "lists"
    postings = [{
        "id": "x", "text": "Account Manager", "categories": {}, "hostedUrl": "https://jobs.lever.co/x",
        "descriptionPlain": "Intro.",
        "lists": [{"text": "REQUIRED QUALIFICATIONS:", "content": "<li>5+ years in sales</li><li>CRM</li>"}],
    }]
    with patch("collectors.lever.requests.get", return_value=_FakeResp(postings)):
        d = fetch_lever("x")[0]["descricao"]
    assert "Intro." in d and "REQUIRED QUALIFICATIONS: 5+ years in sales CRM" in d

    # preferências: só referenciadas nas perguntas quando existem
    assert all("preferencias" not in q["instructions"] for q in build_questions().values())
    assert all(q["instructions"]["preferencias"] == "`preferencias`" for q in build_questions(True).values())
    print("OK  test_descricao_completa")


def test_shopee_tiktok_parsing():
    # Shopee: formato copiado das respostas reais de 25/09/2026. Só a região 13
    # (Brasil) entra; o filtro de título roda antes de buscar a descrição.
    pages = [
        {"positions": [
            {"id": 1, "position_name": "Analista de Marketplace Sênior", "region": 13},
            {"id": 2, "position_name": "Operador de Empilhadeira - Louveira (SP)", "region": 13},
            {"id": 3, "position_name": "Key Account Manager (Mandarin Speaker)", "region": 10},
        ], "meta": {"record_count": 53}},  # 53 = 50 na 1ª página + 3 na 2ª (simplificado)
        {"positions": [{"id": 4, "position_name": "Seller Growth Strategist", "region": 13}], "meta": {"record_count": 53}},
    ]
    detalhe = {"position_presentation": [{"position_description": json.dumps(
        {"job_description": "<ul><li>Gerir sellers</li></ul>", "job_requirement": "<ul><li>Excel</li></ul>"})}]}
    detalhes_buscados = []

    def fake_get(url, params=None, headers=None, timeout=None):
        if url == SHOPEE_SEARCH_URL:
            return _FakeResp(pages[params["offset"] // 50])
        detalhes_buscados.append(params["id"])
        return _FakeResp(detalhe)

    with patch("collectors.shopee.requests.get", side_effect=fake_get):
        jobs = fetch_shopee(term_filter=["marketplace", "seller"])
    assert [j["titulo"] for j in jobs] == ["Analista de Marketplace Sênior", "Seller Growth Strategist"]
    assert detalhes_buscados == [1, 4]  # empilhadeira e vaga das Filipinas nem tiveram detalhe buscado
    assert jobs[0]["descricao"] == "Gerir sellers\n\nRequisitos: Excel"
    assert jobs[0]["link"] == "https://careers.shopee.com.br/job-detail/1/"

    # TikTok: lista já vem com descrição e requisitos
    payload = {"data": {"count": 2, "job_post_list": [
        {"id": "77", "title": "Client Solutions Manager - Marketplace - São Paulo", "description": "Desc.",
         "requirement": "Req.", "city_info": {"code": "CT_130", "en_name": "Sao Paulo"}},
        {"id": "78", "title": "Backend Engineer", "description": "x", "requirement": "", "city_info": {"code": "CT_130"}},
    ]}}
    with patch("collectors.tiktok.requests.post", return_value=_FakeResp(payload)) as m:
        jobs = fetch_tiktok(term_filter=["marketplace"])
    assert m.call_args.kwargs["headers"]["website-path"] == "tiktok"
    assert m.call_args.kwargs["json"]["location_code_list"] == ["CT_130", "CT_1102325"]
    assert len(jobs) == 1
    assert jobs[0]["cidade"] == "São Paulo" and jobs[0]["descricao"] == "Desc.\n\nRequisitos: Req."
    assert jobs[0]["link"] == "https://lifeattiktok.com/search/77"
    print("OK  test_shopee_tiktok_parsing")


def test_workday_smartrecruiters_parsing():
    # Workday: formato copiado da resposta real (PayPal/Rappi, 29/09/2026) —
    # facets de localização aninhados; só o Brasil entra no filtro.
    assert parse_site("https://natura.wd501.myworkdayjobs.com/pt-BR/NaturaCarreiras") == (
        "natura.wd501.myworkdayjobs.com", "natura", "NaturaCarreiras")
    facets = [{"facetParameter": "locationMainGroup", "values": [
        {"facetParameter": "locations", "values": [
            {"descriptor": "BRA-São Paulo", "id": "sp1", "count": 2},
            {"descriptor": "PER-Lima", "id": "lim", "count": 14},
        ]}]}]
    listas = []

    def fake_post(url, json=None, headers=None, timeout=None):
        listas.append(json["appliedFacets"])
        return _FakeResp({"total": 2, "facets": facets, "jobPostings": [
            {"title": "Sr Analyst, Revenue Operations", "externalPath": "/job/SP/Sr-Analyst_R1"},
            {"title": "Picker", "externalPath": "/job/SP/Picker_R2"},
        ]})

    def fake_get(url, headers=None, timeout=None):
        assert url.endswith("/job/SP/Sr-Analyst_R1")  # o Picker nem teve detalhe buscado
        return _FakeResp({"jobPostingInfo": {"title": "Sr Analyst, Revenue Operations",
                                             "jobDescription": "<p>RevOps LATAM</p>", "location": "Sao Paulo, Brazil",
                                             "externalUrl": "https://paypal.example/job/R1"}})

    with patch("collectors.workday.requests.post", side_effect=fake_post), \
         patch("collectors.workday.requests.get", side_effect=fake_get):
        jobs = fetch_workday("https://paypal.wd1.myworkdayjobs.com/jobs", "PayPal", term_filter=["revenue"])
    assert listas[1] == {"locations": ["sp1"]}  # a 2ª chamada já vai filtrada pelo Brasil
    assert len(jobs) == 1 and jobs[0]["empresa"] == "PayPal"
    assert jobs[0]["descricao"] == "RevOps LATAM" and jobs[0]["link"] == "https://paypal.example/job/R1"

    # SmartRecruiters
    lista = {"totalFound": 2, "content": [
        {"id": "1", "name": "Business Development - Crédito", "location": {"city": "São Paulo", "remote": False}},
        {"id": "2", "name": "Agente de Atendimento II", "location": {"city": "São Carlos"}},
    ]}
    det = {"postingUrl": "https://jobs.smartrecruiters.com/Experian/1", "jobAd": {"sections": {
        "jobDescription": {"text": "<p>Originar negócios</p>"}, "qualifications": {"text": "<ul><li>Crédito</li></ul>"}}}}

    def fake_sr(url, params=None, timeout=None):
        if params is not None:
            assert params["country"] == "br"
            return _FakeResp(lista)
        return _FakeResp(det)

    with patch("collectors.smartrecruiters.requests.get", side_effect=fake_sr):
        jobs = fetch_smartrecruiters("Experian", "Serasa Experian", term_filter=["business"])
    assert len(jobs) == 1 and jobs[0]["empresa"] == "Serasa Experian"
    assert jobs[0]["descricao"] == "Originar negócios\n\nCrédito"
    # Teamtailor: RSS copiado (simplificado) do feed real da Jet Brasil, 29/09/2026
    rss = """<?xml version="1.0"?><rss xmlns:tt="https://teamtailor.com/locations"><channel>
      <item><title>Gerente de E-commerce (Marketplace) e Importação</title>
        <description>&lt;p&gt;Gerir &lt;strong&gt;marketplaces&lt;/strong&gt;&lt;/p&gt;</description>
        <pubDate>Wed, 26 Aug 2026 10:11:16 -0300</pubDate>
        <link>https://careers.jetshr.com.br/jobs/688967-gerente</link><remoteStatus>none</remoteStatus>
        <tt:locations><tt:location><tt:city>São Paulo</tt:city></tt:location></tt:locations></item>
      <item><title>Mecânico I</title><description>x</description><link>https://careers.jetshr.com.br/jobs/1</link></item>
    </channel></rss>"""
    with patch("collectors.teamtailor.requests.get", return_value=_FakeResp(None, content=rss.encode())) as m:
        jobs = fetch_teamtailor("https://careers.jetshr.com.br/", "Jet Brasil", term_filter=["marketplace"])
    assert m.call_args.args[0] == "https://careers.jetshr.com.br/jobs.rss"
    assert len(jobs) == 1
    j = jobs[0]
    assert j["descricao"] == "Gerir marketplaces" and j["cidade"] == "São Paulo"
    assert j["publicado_em"] == "2026-08-26" and j["remoto"] is False
    print("OK  test_workday_smartrecruiters_parsing (+ teamtailor)")


def test_ajuste_junior():
    def rec(titulo, recomendacao="aplicar_agora"):
        return ajustar_recomendacao({"titulo": titulo, "recomendacao": recomendacao})

    for t in ["Analista de Planejamento e Performance Júnior", "Analista Jr de Marketplace",
              "Junior Business Analyst", "Assistente de E-commerce Junior"]:
        r = rec(t)
        assert r["recomendacao"] == "avaliar" and r["ajuste"], t
    # gestão com "júnior", analista não-júnior e quem já não era aplicar_agora: intocados
    for t in ["Gestor(a) de Categorias Júnior – Marketplace", "Coordenador Júnior de Growth",
              "Analista de Revenue Operations Pleno", "Analista de Marketplace",
              "Especialista Desenvolvimento de Negócios I", "Especialista Júnior de Parcerias"]:
        r = rec(t)
        assert r["recomendacao"] == "aplicar_agora" and r["ajuste"] == "", t
    assert rec("Analista Júnior", "nao_aplicar")["recomendacao"] == "nao_aplicar"
    # paga em dólar: júnior não é rebaixado (júnior em USD > sênior no Brasil)
    for extra in [{"fonte": "remoteok"}, {"salario": "US$ 60,000 a US$ 80,000 /ano"},
                  {"descricao": "Compensation: $2,500/month, paid in USD."}]:
        r = ajustar_recomendacao({"titulo": "Junior Business Analyst", "recomendacao": "aplicar_agora", **extra})
        assert r["recomendacao"] == "aplicar_agora" and r["ajuste"] == "", extra
    # salário em real não é dólar
    r = ajustar_recomendacao({"titulo": "Analista Jr de Marketplace", "recomendacao": "aplicar_agora",
                              "salario": "R$ 5.000 a R$ 6.000", "descricao": "Salário R$ 5.000."})
    assert r["recomendacao"] == "avaliar"
    print("OK  test_ajuste_junior")


def test_cache_versao():
    cv_text = "CV de teste com revenue operations"
    job = {
        "id": "fake:v", "fonte": "fake", "titulo": "Analista de Revenue Operations",
        "empresa": "EmpresaX", "cidade": "SP", "estado": "", "remoto": False,
        "link": "https://example.com/v", "publicado_em": "", "descricao": "Revenue Operations.",
    }
    q = build_questions()
    v1 = scoring_version(cv_text, "", q)
    assert v1 == scoring_version(cv_text, "", q)  # determinística
    assert v1 != scoring_version(cv_text, "não quero vendas", build_questions(True))
    assert v1 != scoring_version(cv_text + " atualizado", "", q)

    _, cache1, _, pont1 = score_jobs_with_cache(MockJevClient(), cv_text, [job], q, {}, versao=v1)
    assert pont1 == 1 and cache1[normalize_key(job)]["_versao"] == v1
    # mesma versão -> reaproveita
    _, _, reap, pont = score_jobs_with_cache(MockJevClient(), cv_text, [job], q, cache1, versao=v1)
    assert (reap, pont) == (1, 0)
    # versão nova (ex.: preferências mudaram) -> repontua
    _, _, reap, pont = score_jobs_with_cache(MockJevClient(), cv_text, [job], q, cache1, versao="outra")
    assert (reap, pont) == (0, 1)
    print("OK  test_cache_versao")


def test_cache_reuse():
    cv_text = Path("cv.txt").read_text(encoding="utf-8")
    questions = build_questions()

    job_a = {
        "id": "fake:a", "fonte": "fake", "titulo": "Analista de Revenue Operations",
        "empresa": "EmpresaX", "cidade": "São Paulo", "estado": "SP", "remoto": False,
        "link": "https://example.com/a", "publicado_em": "2026-09-01",
        "descricao": "Revenue Operations, forecasting, pipeline, métricas SaaS.",
    }
    job_b = {
        "id": "fake:b", "fonte": "fake", "titulo": "Engenheiro de Software Backend",
        "empresa": "TechY", "cidade": "Remoto", "estado": "", "remoto": True,
        "link": "https://example.com/b", "publicado_em": "2026-09-02",
        "descricao": "Backend, Java, Kubernetes, microsserviços.",
    }

    # 1ª rodada: cache vazio -> as duas vagas são pontuadas pelo Jev de verdade.
    client = MockJevClient()
    results1, cache1, reaproveitadas1, pontuadas1 = score_jobs_with_cache(
        client, cv_text, [job_a, job_b], questions, {}
    )
    assert reaproveitadas1 == 0
    assert pontuadas1 == 2
    assert len(cache1) == 2
    assert normalize_key(job_a) in cache1
    for f in CACHE_SCORE_FIELDS:
        assert f in cache1[normalize_key(job_a)]

    # 2ª rodada: mesmas duas vagas de novo (coleta idêntica) -> nenhuma
    # chamada nova ao Jev deveria ser necessária; um client "quebrado" prova
    # isso, já que qualquer chamada real levantaria erro.
    class _BrokenClient:
        def evaluate(self, *a, **kw):
            raise AssertionError("Jev foi chamado para uma vaga que já estava no histórico")

    results2, cache2, reaproveitadas2, pontuadas2 = score_jobs_with_cache(
        _BrokenClient(), cv_text, [job_a, job_b], questions, cache1
    )
    assert reaproveitadas2 == 2
    assert pontuadas2 == 0
    assert {r["nota_fit"] for r in results2} == {r["nota_fit"] for r in results1}

    # 3ª rodada: job_b "encerrou" (não veio mais na coleta) e uma vaga nova
    # (job_c) apareceu -> job_c é pontuada, job_b some do novo cache/CSV.
    job_c = {
        "id": "fake:c", "fonte": "fake", "titulo": "Analista de Growth Sênior",
        "empresa": "OutraCo", "cidade": "São Paulo", "estado": "SP", "remoto": False,
        "link": "https://example.com/c", "publicado_em": "2026-09-05",
        "descricao": "Growth, aquisição, funil, experimentação.",
    }
    results3, cache3, reaproveitadas3, pontuadas3 = score_jobs_with_cache(
        MockJevClient(), cv_text, [job_a, job_c], questions, cache2
    )
    assert reaproveitadas3 == 1  # job_a
    assert pontuadas3 == 1  # job_c
    assert normalize_key(job_b) not in cache3  # encerrada -> some do histórico
    assert normalize_key(job_a) in cache3
    assert normalize_key(job_c) in cache3

    # load_cache/save_cache: round-trip em disco.
    tmp_path = Path("output/_selftest_historico.json")
    save_cache(tmp_path, cache3)
    reloaded = load_cache(tmp_path)
    assert reloaded == cache3
    tmp_path.unlink()

    print("OK  test_cache_reuse")


def test_scoring_and_ranking_end_to_end():
    cv_text = Path("cv.txt").read_text(encoding="utf-8")
    client = MockJevClient()
    questions = build_questions()

    fake_jobs = [
        {
            "id": "fake:1",
            "fonte": "fake",
            "titulo": "Analista de Business Development e Parcerias",
            "empresa": "EmpresaX",
            "cidade": "São Paulo",
            "estado": "SP",
            "remoto": False,
            "link": "https://example.com/1",
            "descricao": (
                "Buscamos analista de Business Development para liderar Go-to-Market, "
                "gestão de pipeline de parcerias estratégicas, análise de mercado TAM/SAM "
                "e relacionamento com parceiros de tecnologia."
            ),
            "publicado_em": "2026-09-01",
        },
        {
            "id": "fake:2",
            "fonte": "fake",
            "titulo": "Engenheiro de Software Backend Sênior (Java/Kubernetes)",
            "empresa": "TechY",
            "cidade": "Remoto",
            "estado": "",
            "remoto": True,
            "link": "https://example.com/2",
            "descricao": (
                "Vaga para engenheiro backend sênior, 8+ anos com Java, Kubernetes, "
                "microsserviços, arquitetura distribuída. Não é uma vaga de negócios."
            ),
            "publicado_em": "2026-09-02",
        },
        {
            "id": "fake:3",
            "fonte": "fake",
            "titulo": "Analista de Revenue Operations Pleno",
            "empresa": "SaaSCo",
            "cidade": "São Paulo",
            "estado": "SP",
            "remoto": False,
            "link": "https://example.com/3",
            "descricao": (
                "Revenue Operations: forecasting, pipeline, dashboards de BI, métricas "
                "SaaS (MRR, ARR, CAC, LTV), reporting e P&L."
            ),
            "publicado_em": "2026-09-03",
        },
    ]

    results = [score_job(client, cv_text, j, questions) for j in fake_jobs]
    by_id = {r["id"]: r for r in results}

    # A vaga de engenharia de software deve pontuar claramente pior que as de negócios.
    assert by_id["fake:2"]["nota_fit"] < by_id["fake:1"]["nota_fit"]
    assert by_id["fake:2"]["nota_fit"] < by_id["fake:3"]["nota_fit"]

    for r in results:
        assert 0 <= r["nota_fit"] <= 10
        assert r["recomendacao"] in {"aplicar_agora", "avaliar", "baixa_prioridade", "nao_aplicar"}
        assert r["trilha"] in {
            "revenue_operations", "gtm_bd", "business_analyst_estrategia",
            "ecommerce_marketplace", "produto_ops", "outro",
        }

    # Testa também a escrita do CSV com as mesmas colunas do screen.py
    out_path = Path("output/_selftest.csv")
    out_path.parent.mkdir(exist_ok=True)
    columns = [
        "recomendacao", "nota_fit", "aderencia_nivel", "confianca", "senioridade_ok",
        "gap_critico", "trilha", "titulo", "empresa", "cidade", "estado", "remoto",
        "fonte", "publicado_em", "link",
    ]
    with out_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for r in results:
            writer.writerow({c: r.get(c, "") for c in columns})

    with out_path.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 3
    out_path.unlink()

    print("OK  test_scoring_and_ranking_end_to_end")
    for r in results:
        print(f"     [{r['recomendacao']:15s}] {r['nota_fit']:4.1f}  {r['titulo']}")


def test_bebee_descricao_completa_repontua():
    cv_text = Path("cv.txt").read_text(encoding="utf-8")
    questions = build_questions()
    versao = "v1"

    def vaga():
        return {"id": "bebee:x", "fonte": "bebee", "titulo": "Analista de RevOps Sênior", "empresa": "Acme",
                "cidade": "São Paulo", "estado": "", "remoto": False, "link": "https://bebee.com/br/jobs/x",
                "publicado_em": "Hoje", "descricao": "Sobre a vaga Você vai entrar no time... Não buscamos"}

    # nota antiga, dada só com o resumo de 300 caracteres
    _, cache, _, _ = score_jobs_with_cache(MockJevClient(), cv_text, [vaga()], questions, {}, versao=versao)
    assert "_desc_completa" not in cache[normalize_key(vaga())]

    # rodada nova: busca a página, e a nota antiga é refeita uma vez
    job = vaga()
    with patch("collectors.bebee.fetch_bebee_descricao", return_value="Requisitos: SQL avançado. " * 50) as m:
        assert completar_descricoes_bebee([job], cache, versao) == 1
    assert m.call_count == 1 and job["_desc_completa"] and job["descricao"].startswith("Requisitos")
    _, cache2, reaproveitadas, pontuadas = score_jobs_with_cache(
        MockJevClient(), cv_text, [job], questions, cache, versao=versao)
    assert (reaproveitadas, pontuadas) == (0, 1)
    assert cache2[normalize_key(job)]["_desc_completa"] is True

    # rodada seguinte: nada a baixar, nota reaproveitada, marca mantida
    job3 = vaga()
    with patch("collectors.bebee.fetch_bebee_descricao", side_effect=AssertionError("não devia baixar")):
        assert completar_descricoes_bebee([job3], cache2, versao) == 0
    _, cache3, reaproveitadas, pontuadas = score_jobs_with_cache(
        MockJevClient(), cv_text, [job3], questions, cache2, versao=versao)
    assert (reaproveitadas, pontuadas) == (1, 0)
    assert cache3[normalize_key(job3)]["_desc_completa"] is True
    print("OK  test_bebee_descricao_completa_repontua")


def _montar_saida(out: Path, vagas: list[dict]) -> None:
    """Relatório + coleta mínimos, no formato que o screen.py grava."""
    with (out / "vagas_triadas_2026-01-01.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["recomendacao", "nota_fit", "titulo", "empresa", "fonte", "link"])
        w.writeheader()
        for v in vagas:
            w.writerow({k: v[k] for k in w.fieldnames})
    (out / "vagas_coletadas.json").write_text(
        json.dumps([{"link": v["link"], "descricao": v["descricao"]} for v in vagas]), encoding="utf-8")


def test_nuvem_e_sincronizar():
    import importlib.util

    import sincronizar as sinc

    raiz = Path(__file__).parent
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        env = {"SQLITE_PATH": str(out / "nuvem.db"), "PAINEL_SENHA": "s3nha", "PAINEL_TOKEN": "tok",
               "SEGREDO": "x" * 32}
        with patch.dict(os.environ, env):
            os.environ.pop("DATABASE_URL", None)
            os.environ.pop("POSTGRES_URL", None)
            spec = importlib.util.spec_from_file_location("painel_nuvem", raiz / "web" / "api" / "index.py")
            web = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(web)
            navegador = web.criar_app().test_client()
            pc_cliente = web.criar_app().test_client()

            class _NuvemTeste(sinc.Nuvem):
                def __init__(self):
                    pass

                def _req(self, metodo, caminho, json=None):
                    r = pc_cliente.open(caminho, method=metodo, json=json,
                                        headers={"Authorization": "Bearer tok"})
                    assert r.status_code == 200, (caminho, r.get_json())
                    return r.get_json()

            _montar_saida(out, [
                {"recomendacao": "aplicar_agora", "nota_fit": "9.1", "titulo": "Analista de RevOps",
                 "empresa": "Acme", "fonte": "gupy", "link": "https://x/1", "descricao": ""},
                {"recomendacao": "avaliar", "nota_fit": "6.0", "titulo": "Analista de Growth",
                 "empresa": "Beta", "fonte": "gupy", "link": "https://x/2", "descricao": ""},
                {"recomendacao": "nao_aplicar", "nota_fit": "2.0", "titulo": "Engenheiro Java",
                 "empresa": "Gama", "fonte": "gupy", "link": "https://x/3", "descricao": ""},
            ])
            config = {"output_dir": str(out)}
            nuvem = _NuvemTeste()

            # sem senha, nada; com o token errado, o PC também não entra
            assert navegador.get("/api/painel").status_code == 401
            assert navegador.post("/api/login", json={"senha": "errada"}).status_code == 401
            assert pc_cliente.get("/api/pc/estado", headers={"Authorization": "Bearer x"}).status_code == 401
            assert navegador.post("/api/login", json={"senha": "s3nha"}).status_code == 200

            # 1ª rodada: sobe as vagas do dia ("nao_aplicar" fica de fora)
            assert sinc.sincronizar(config, nuvem) == {"vagas": 2, "candidaturas": 0}
            p = navegador.get("/api/painel").get_json()
            assert [v["titulo"] for v in p["vagas"]] == ["Analista de RevOps", "Analista de Growth"]
            assert p["pc_visto_em"] and p["relatorio"] == "vagas_triadas_2026-01-01.csv"

            # "Já enviei" numa vaga do dia e o formulário (vaga de fora do relatório)
            assert navegador.post("/api/candidatura", json={"titulo": "BA"}).status_code == 400
            assert navegador.post("/api/candidatura", json={"titulo": "BA", "empresa": "Delta",
                                                            "enviada_em": "ontem"}).status_code == 400
            assert navegador.post("/api/candidatura", json=p["vagas"][0]).status_code == 200
            assert navegador.post("/api/candidatura", json={"titulo": "Business Analyst", "empresa": "Delta",
                                                            "enviada_em": "2026-10-20",
                                                            "notas": "pelo LinkedIn"}).status_code == 200
            p = navegador.get("/api/painel").get_json()
            por_link = {c["link"]: c for c in p["candidaturas"]}
            acme = por_link["https://x/1"]
            assert acme["status"] == "enviada" and acme["nota_fit"] == "9.1" and acme["enviada_em"]
            assert [v["status"] for v in p["vagas"]] == ["enviada", ""]
            manual = next(c for l, c in por_link.items() if l.startswith("manual:"))
            assert (manual["empresa"], manual["fonte"], manual["enviada_em"], manual["notas"]) == \
                ("Delta", "manual", "2026-10-20", "pelo LinkedIn")

            # status, anotação e exclusão de um registro feito por engano
            assert navegador.post("/api/status", json={"id": acme["id"], "status": "entrevista"}).status_code == 200
            assert navegador.post("/api/status", json={"id": acme["id"], "status": "pronta"}).status_code == 400
            assert navegador.post("/api/notas", json={"id": acme["id"], "notas": "falar com a Ana"}).status_code == 200
            assert navegador.post("/api/excluir", json={"id": manual["id"]}).status_code == 200
            assert navegador.post("/api/excluir", json={"id": "naoexiste"}).status_code == 404

            # 2ª rodada: o relatório não mudou, então não sobe as vagas de novo; a cópia
            # no PC reflete o painel
            assert sinc.sincronizar(config, nuvem) == {"vagas": 0, "candidaturas": 1}
            copia = json.loads((out / "candidaturas.json").read_text(encoding="utf-8"))
            assert len(copia) == 1 and copia[0]["status"] == "entrevista" and copia[0]["notas"] == "falar com a Ana"
            assert [h["status"] for h in copia[0]["historico"]] == ["enviada", "entrevista"]
    print("OK  test_nuvem_e_sincronizar")


if __name__ == "__main__":
    test_gupy_parsing()
    test_remotive_parsing()
    test_greenhouse_parsing()
    test_inhire_parsing()
    test_lever_parsing()
    test_ashby_parsing()
    test_bebee_parsing()
    test_amazon_parsing()
    test_mercadolivre_parsing()
    test_weworkremotely_parsing()
    test_aceita_brasil()
    test_remoteok_remoteio_parsing()
    test_didi_parsing()
    test_workable_parsing()
    test_title_filter()
    test_descricao_completa()
    test_shopee_tiktok_parsing()
    test_workday_smartrecruiters_parsing()
    test_ajuste_junior()
    test_cache_versao()
    test_cache_reuse()
    test_bebee_descricao_completa_repontua()
    test_scoring_and_ranking_end_to_end()
    test_nuvem_e_sincronizar()
    print("\nTodos os testes offline passaram.")

#!/usr/bin/env python3
"""
Descobre em qual sistema de vagas (ATS) uma empresa publica, pra adicioná-la
ao config.yaml. Apoio ao comando /radar.

Dois modos:
    python descobrir_ats.py --link "https://careers.empresa.com.br/jobs/123-vaga"
        -> olha a URL e a página da vaga e diz qual ATS é (Gupy, InHire,
           Workday, Teamtailor, Greenhouse, Lever, Ashby, SmartRecruiters...)
    python descobrir_ats.py nomeempresa [outronome ...]
        -> testa esses nomes como identificador em cada ATS que o script lê e
           mostra onde existe board e quantas vagas tem

Um board existir não garante que é a empresa certa (ex.: "via" no Greenhouse é
a Via Transportation, dos EUA) — sempre confira as vagas antes de adicionar.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import re
from urllib.parse import urlparse

import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128.0 Safari/537.36"}

# (padrão na URL, ATS, fonte no config.yaml)
PELA_URL = [
    (r"\.gupy\.io", "Gupy", "sources.gupy.empresas (nome da career page)"),
    (r"\.inhire\.app", "InHire", "sources.inhire.tenants"),
    (r"myworkdayjobs\.com|myworkdaysite\.com", "Workday", "sources.workday.sites"),
    (r"teamtailor\.com", "Teamtailor", "sources.teamtailor.sites"),
    (r"greenhouse\.io", "Greenhouse", "sources.greenhouse.boards"),
    (r"lever\.co", "Lever", "sources.lever.boards"),
    (r"ashbyhq\.com", "Ashby", "sources.ashby.boards"),
    (r"smartrecruiters\.com", "SmartRecruiters", "sources.smartrecruiters.empresas"),
    (r"\.recruitee\.com", "Recruitee", "sources.recruitee.empresas (id = subdomínio)"),
    (r"\.jobs\.personio\.(de|com)", "Personio", "sources.personio.empresas (id = subdomínio)"),
    (r"\.breezy\.hr", "Breezy HR", "sources.breezy.empresas (id = subdomínio)"),
    (r"\.pinpointhq\.com", "Pinpoint", "sources.pinpoint.empresas (id = subdomínio)"),
]
# marcas de cada ATS no HTML, pra sites em domínio próprio da empresa
PELO_HTML = [
    (r"/jobs\.rss|teamtailor", "Teamtailor", "sources.teamtailor.sites (url = raiz do site)"),
    (r"gupy\.io|gupy_portal", "Gupy", "sources.gupy.empresas"),
    (r"inhire\.app", "InHire", "sources.inhire.tenants"),
    (r"myworkdayjobs|wday/cxs", "Workday", "sources.workday.sites"),
    (r"greenhouse\.io", "Greenhouse", "sources.greenhouse.boards"),
    (r"lever\.co", "Lever", "sources.lever.boards"),
    (r"ashbyhq", "Ashby", "sources.ashby.boards"),
    (r"smartrecruiters", "SmartRecruiters", "sources.smartrecruiters.empresas"),
    (r"recruitee\.com", "Recruitee", "sources.recruitee.empresas"),
    (r"jobs\.personio\.", "Personio", "sources.personio.empresas"),
    (r"breezy\.hr", "Breezy HR", "sources.breezy.empresas"),
    (r"pinpointhq\.com", "Pinpoint", "sources.pinpoint.empresas"),
    (r"eightfold", "Eightfold (não suportado)", "-"),
    (r"successfactors|sapsf", "SAP SuccessFactors (não suportado)", "-"),
    (r"recrut\.ai", "Recrut.ai (não suportado)", "-"),
    (r"solides|vagas\.solides", "Sólides (não suportado)", "-"),
]


def pelo_link(link: str) -> None:
    host = urlparse(link).netloc
    for pad, ats, onde in PELA_URL:
        if re.search(pad, host):
            print(f"{ats} (pela URL) -> adicionar em {onde}")
            return
    try:
        html = requests.get(link, headers=UA, timeout=30).text
    except requests.RequestException as e:
        print(f"Não consegui abrir a página: {e}")
        return
    achados = [(ats, onde) for pad, ats, onde in PELO_HTML if re.search(pad, html, re.I)]
    if not achados:
        print("Nenhuma marca de ATS conhecido na página — provavelmente site próprio ou ATS não suportado.")
    for ats, onde in achados:
        print(f"{ats} (pelo HTML) -> {onde}")


def _probe(nome: str) -> tuple[str, list[str]]:
    out = []
    testes = [
        ("greenhouse", f"https://boards-api.greenhouse.io/v1/boards/{nome}/jobs",
         lambda r: len(r.json().get("jobs", []))),
        ("lever", f"https://api.lever.co/v0/postings/{nome}?mode=json",
         lambda r: len(r.json()) if isinstance(r.json(), list) else None),
        ("ashby", f"https://api.ashbyhq.com/posting-api/job-board/{nome}",
         lambda r: len(r.json().get("jobs", []))),
        ("smartrecruiters (br)", f"https://api.smartrecruiters.com/v1/companies/{nome}/postings?country=br&limit=1",
         lambda r: r.json().get("totalFound")),
        ("teamtailor", f"https://{nome}.teamtailor.com/jobs.rss",
         lambda r: r.text.count("<item>") if "<rss" in r.text else None),
        # ATSs de agências/PMEs (07/11/2026, fontes-vagas.md)
        ("recruitee", f"https://{nome}.recruitee.com/api/offers/",
         lambda r: len(r.json().get("offers", []))),
        ("personio", f"https://{nome}.jobs.personio.de/xml",
         lambda r: r.text.count("<position>") if "workzag-jobs" in r.text else None),
        ("breezy", f"https://{nome}.breezy.hr/json",
         lambda r: len(r.json()) if isinstance(r.json(), list) else None),
        ("pinpoint", f"https://{nome}.pinpointhq.com/postings.json",
         lambda r: len(r.json().get("data", []))),
    ]
    for ats, url, contar in testes:
        try:
            r = requests.get(url, headers=UA, timeout=15)
            if r.status_code == 200:
                n = contar(r)
                if n is not None:
                    out.append(f"{ats}({n})")
        except (requests.RequestException, ValueError):
            pass
    try:
        r = requests.get("https://api.inhire.app/job-posts/public/pages", headers={"X-Tenant": nome}, timeout=15)
        if r.status_code == 200:
            n = len([j for j in r.json().get("jobsPage", []) if j.get("status") == "published"])
            out.append(f"inhire({n})")
    except (requests.RequestException, ValueError):
        pass
    return nome, out


def pelo_nome(nomes: list[str]) -> None:
    with cf.ThreadPoolExecutor(8) as ex:
        for nome, out in ex.map(_probe, nomes):
            print(f"{nome:20s} {', '.join(out) if out else 'nada encontrado'}")
    # Gupy: busca por nome de empresa no MCP (a Gupy não usa slug)
    try:
        from collectors import gupy
        for nome in nomes:
            res = gupy._mcp_call("tools/call", {"name": "list_companies", "arguments": {"term": nome, "limit": "5"}})
            d = json.loads(res["content"][0]["text"])
            while isinstance(d, dict) and "data" in d:
                d = d["data"]
            nomes_gupy = sorted({c.get("careerPageName") for c in d if c.get("careerPageName")})
            if nomes_gupy:
                print(f"{nome:20s} gupy: {nomes_gupy} (use o nome exato em sources.gupy.empresas)")
    except Exception as e:  # noqa: BLE001
        print(f"(busca na Gupy falhou: {e})")
    print("\nWorkday não dá pra testar pelo nome (o endereço varia: <empresa>.wdN.myworkdayjobs.com/<site>) — "
          "busque '<empresa> myworkdayjobs' e use --link.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("nomes", nargs="*")
    ap.add_argument("--link")
    args = ap.parse_args()
    if args.link:
        pelo_link(args.link)
    if args.nomes:
        pelo_nome(args.nomes)
    if not args.link and not args.nomes:
        ap.print_help()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Mapa de lacunas: o que as vagas boas pedem e o seu CV não mostra (ou mostra
pouco). Ideia do /upskill do ai-job-search (06/11/2026), feita sem IA: o Jev
diz se falta um requisito obrigatório (`gap_critico`), mas não diz qual — este
script conta, nas vagas "aplicar_agora" e "avaliar" do último relatório, quais
ferramentas e competências aparecem na descrição e compara com o cv.txt.

Uso:
    python lacunas.py                 # relatório mais recente em output/
    python lacunas.py --todas         # todas as vagas pontuadas, não só as boas
    python lacunas.py --trilha revenue_operations

Saída: tabela no terminal + output/lacunas_<data>.csv. O sincronizar.py
calcula o mesmo mapa a cada rodada e manda para a aba "Lacunas" do painel.

Colunas:
    vagas        em quantas vagas boas o termo aparece (e % do total)
    obrigatorio  das quais o termo NÃO aparece perto de "desejável",
                 "diferencial", "nice to have", "plus" etc.
    com_gap      % das vagas com gap crítico (segundo o Jev) que citam o termo
    sem_gap      % das vagas sem gap crítico que citam o termo — se com_gap é
                 bem maior, o termo provavelmente é o que derruba a nota
    no_cv        o termo como aparece no cv.txt ("" = não aparece)

Os termos ficam em TERMOS abaixo; dá pra trocar ou completar pelo bloco
`lacunas` do config.yaml (nome: regex, aplicada ao texto em minúsculas e sem
acento).
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
import sys
from pathlib import Path

import yaml

from collectors._util import _fold
from screen import normalize_key

# nome -> regex (texto em minúsculas e sem acento, ver _fold)
TERMOS = {
    "SQL": r"\bsql\b",
    "Python": r"\bpython\b",
    "R (estatística)": r"\br\b(?= ?[,/)]| e python| and python| ou python)|\brstudio\b",
    "Power BI": r"\bpower ?bi\b",
    "Tableau": r"\btableau\b",
    "Looker / Looker Studio": r"\blooker\b|\bdata studio\b",
    "Excel avançado": r"excel (avancado|advanced)|advanced excel|excel \(avancado|proficien\w* (in|with) excel|vba|macros?\b",
    "Google Sheets / Apps Script": r"google sheets|apps script",
    "BigQuery / Snowflake / Redshift": r"\bbigquery\b|\bsnowflake\b|\bredshift\b|\bdatabricks\b",
    "dbt / modelagem de dados": r"\bdbt\b|modelagem de dados|data model(l)?ing",
    "Estatística / testes A/B": r"estatistic|statistic|\ba/b\b|teste a/?b|experimenta(cao|tion)",
    "Forecasting / planejamento": r"forecast|previsao de (vendas|receita|demanda)|planejamento (comercial|de vendas|financeiro)|fp&a",
    "Modelagem financeira / valuation": r"modelagem financeira|financial model|valuation|\bdcf\b|business case",
    "Salesforce": r"\bsalesforce\b|\bsfdc\b",
    "HubSpot": r"\bhubspot\b",
    "Pipedrive": r"\bpipedrive\b",
    "CRM (genérico)": r"\bcrm\b",
    "Gong / Clari / Outreach": r"\bgong\b|\bclari\b|\boutreach\.io\b|\bsalesloft\b",
    "SAP / ERP": r"\bsap\b|\berp\b|\btotvs\b|\bprotheus\b|\boracle\b",
    "Jira / Asana / Notion": r"\bjira\b|\basana\b|\bnotion\b|\bmonday\.com\b",
    "Google Analytics / GA4": r"google analytics|\bga4\b",
    "Mídia paga (Google/Meta Ads)": r"google ads|meta ads|facebook ads|midia paga|paid media|performance marketing|marketing de performance",
    "SEO / CRM marketing": r"\bseo\b|marketing automation|automacao de marketing|\bbraze\b|\bklaviyo\b",
    "Amazon Seller / Vendor Central": r"seller central|vendor central|amazon ads",
    "Marketplace (ML, Amazon, Shopee…)": r"\bmarketplaces?\b|mercado livre|\bshopee\b|\bmagalu\b",
    "Shopify / VTEX / plataformas": r"\bshopify\b|\bvtex\b|\bwoocommerce\b|\bmagento\b|\bnuvemshop\b",
    "Pricing / precificação": r"\bpricing\b|precificac",
    "SaaS / métricas (MRR, CAC, LTV)": r"\bsaas\b|\bmrr\b|\barr\b|\bcac\b|\bltv\b|\bchurn\b|\bnrr\b",
    "OKRs / KPIs": r"\bokrs?\b|\bkpis?\b",
    "Gestão de pessoas / liderança": r"gestao de (pessoas|equipe|time)|lideranca de (equipe|time)|people management|manage (a )?team|direct reports",
    "Gestão de projetos / PMO": r"gestao de projetos|project management|\bpmo\b|\bpmp\b",
    "Scrum / ágil": r"\bscrum\b|\bagil\b|\bagile\b|\bkanban\b",
    "Inglês fluente / avançado": r"ingles (fluente|avancado)|fluent (in )?english|english \((fluent|advanced)|advanced english|english proficiency|ingles \((fluente|avancado)",
    "Espanhol": r"\bespanhol\b|\bspanish\b",
    "Pós / MBA": r"\bmba\b|pos[- ]gradua|mestrado|master'?s degree",
    "Consultoria (background)": r"consultoria (estrategica|de gestao)|management consulting|big ?4|mckinsey|bain|\bbcg\b",
}

# perto de uma dessas palavras, o termo é diferencial e não obrigatório
_DESEJAVEL_RE = re.compile(
    r"desejave|diferencia|nice to have|bonus|\bplus\b|preferred|preferencial|a good to have|"
    r"good to have|\bideal\b|sera um|will be a|is a plus|e um plus"
)
JANELA = 120  # caracteres antes do termo onde se procura "desejável" etc.
JANELA_DEPOIS = 40  # e depois, até o fim da frase ("Power BI é desejável")


def _diferencial(texto: str, m: re.Match) -> bool:
    depois = re.split(r"[.;]", texto[m.end():m.end() + JANELA_DEPOIS], maxsplit=1)[0]
    return bool(_DESEJAVEL_RE.search(texto[max(0, m.start() - JANELA):m.start()])
                or _DESEJAVEL_RE.search(depois))


def _ler_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def carregar_vagas(out_dir: Path, todas: bool = False, trilha: str = "") -> tuple[str, list[dict]]:
    """(nome do relatório, vagas do relatório com a descrição da coleta)."""
    relatorios = sorted(out_dir.glob("vagas_triadas_*.csv"))
    if not relatorios:
        sys.exit("Nenhum output/vagas_triadas_*.csv — rode o screen.py primeiro.")
    linhas = _ler_csv(relatorios[-1])
    coletadas = out_dir / "vagas_coletadas.json"
    descricoes = {}
    if coletadas.exists():
        for j in json.loads(coletadas.read_text(encoding="utf-8")):
            descricoes[normalize_key(j)] = j.get("descricao") or ""
    vagas = []
    for r in linhas:
        if not todas and r.get("recomendacao") not in ("aplicar_agora", "avaliar"):
            continue
        if trilha and r.get("trilha") != trilha:
            continue
        vagas.append({**r, "descricao": descricoes.get(normalize_key(r), "")})
    return relatorios[-1].name, vagas


def _trecho(texto: str, m: re.Match, depois: int = 16) -> str:
    """O termo como aparece no CV e o que vem logo depois ("sql (basico)"),
    cortado antes de números e separadores — o trecho vai pro painel, e
    telefone/e-mail do cabeçalho do CV não têm o que fazer lá."""
    resto = re.split(r"[\d|·;,.\n@]", texto[m.end():m.end() + depois], maxsplit=1)[0]
    return re.sub(r"\s+", " ", m.group(0) + resto).strip()


def mapa(vagas: list[dict], cv_text: str, termos: dict[str, str]) -> list[dict]:
    """Uma linha por termo, da mais citada para a menos citada."""
    com_desc = [v for v in vagas if v.get("descricao")]
    gap_sim = [v for v in com_desc if v.get("gap_critico") == "sim"]
    gap_nao = [v for v in com_desc if v.get("gap_critico") != "sim"]
    textos = {id(v): _fold(v["descricao"]) for v in com_desc}
    cv_fold = _fold(cv_text)
    linhas = []
    for nome, padrao in termos.items():
        rx = re.compile(padrao)
        vagas_termo = obrig = 0
        citam = set()
        for v in com_desc:
            t = textos[id(v)]
            achados = list(rx.finditer(t))
            if not achados:
                continue
            vagas_termo += 1
            citam.add(id(v))
            # obrigatório se ao menos uma menção não estiver perto de "desejável"
            if any(not _diferencial(t, m) for m in achados):
                obrig += 1
        if not vagas_termo:
            continue
        m_cv = rx.search(cv_fold)
        pct = lambda grupo: round(100 * sum(1 for v in grupo if id(v) in citam) / len(grupo)) if grupo else 0
        linhas.append({
            "termo": nome,
            "vagas": vagas_termo,
            "pct_vagas": round(100 * vagas_termo / len(com_desc)),
            "obrigatorio": obrig,
            "pct_com_gap": pct(gap_sim),
            "pct_sem_gap": pct(gap_nao),
            "no_cv": _trecho(cv_fold, m_cv) if m_cv else "",
        })
    linhas.sort(key=lambda r: -r["vagas"])
    return linhas


def calcular(config: dict, todas: bool = False, trilha: str = "") -> dict:
    """Mapa de lacunas do relatório mais recente, no formato que o painel
    recebe (aba "Lacunas"): {relatorio, vagas, com_gap, linhas}."""
    out_dir = Path(config.get("output_dir", "output"))
    cv_text = Path(config.get("cv_path", "cv.txt")).read_text(encoding="utf-8")
    termos = {**TERMOS, **(config.get("lacunas") or {})}
    relatorio, vagas = carregar_vagas(out_dir, todas, trilha)
    com_desc = [v for v in vagas if v.get("descricao")]
    return {
        "relatorio": relatorio,
        "vagas": len(com_desc),
        "com_gap": sum(1 for v in com_desc if v.get("gap_critico") == "sim"),
        "linhas": mapa(vagas, cv_text, termos),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--todas", action="store_true", help="todas as vagas pontuadas, não só aplicar_agora/avaliar")
    ap.add_argument("--trilha", default="", help="só uma trilha (ex.: revenue_operations)")
    args = ap.parse_args()

    config = yaml.safe_load(Path(args.config).read_text(encoding="utf-8")) or {}
    out_dir = Path(config.get("output_dir", "output"))
    res = calcular(config, args.todas, args.trilha)
    relatorio, linhas, n_desc, n_gap = res["relatorio"], res["linhas"], res["vagas"], res["com_gap"]

    path = out_dir / f"lacunas_{dt.date.today().isoformat()}.csv"
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(linhas[0]) if linhas else ["termo"])
        w.writeheader()
        w.writerows(linhas)

    print(f"Relatório {relatorio}: {n_desc} vagas {'pontuadas' if args.todas else 'boas'} com descrição"
          f"{' na trilha ' + args.trilha if args.trilha else ''}, {n_gap} com gap crítico segundo o Jev.\n")
    print(f"  {'termo':36s} {'vagas':>11s} {'obrig.':>7s} {'c/gap':>6s} {'s/gap':>6s}  no CV")
    for r in linhas:
        cv = r["no_cv"][:40] if r["no_cv"] else "— não aparece"
        print(f"  {r['termo'][:36]:36s} {r['vagas']:5d} ({r['pct_vagas']:2d}%) {r['obrigatorio']:7d} "
              f"{r['pct_com_gap']:5d}% {r['pct_sem_gap']:5d}%  {cv}")
    # termo bem mais citado nas vagas com gap crítico do que nas sem: candidato
    # a ser o requisito que o Jev diz que falta
    suspeitos = [r for r in linhas if r["pct_com_gap"] - r["pct_sem_gap"] >= 8]
    if suspeitos:
        print("\nMais citados quando o Jev aponta requisito não atendido (c/gap - s/gap >= 8 pontos):")
        for r in sorted(suspeitos, key=lambda r: r["pct_sem_gap"] - r["pct_com_gap"]):
            print(f"  {r['termo']}: {r['pct_com_gap']}% x {r['pct_sem_gap']}%"
                  f"{'' if r['no_cv'] else ' (não está no CV)'}")
    print(f"\nTabela salva em {path}.")


if __name__ == "__main__":
    main()

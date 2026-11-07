"""
Perfil 2 do radar: vagas remotas pagas em dólar/euro, em agências e empresas
pequenas e médias do exterior (prompt-perfil-2-radar.md, 07/11/2026).

Roda com `python screen.py --config config_intl.yaml`. O screen.py usa este
módulo quando o config tem `perfil: intl`; o Perfil 1 (config.yaml) não muda.

Aqui ficam só as diferenças do Perfil 2:
- as perguntas e os pesos do Jev (elegibilidade pesa 20%);
- a checagem de elegibilidade na descrição (a vaga aceita quem mora no Brasil?);
- as regras determinísticas por cima da recomendação do Jev.

Regras do Michel que valem aqui (07/11/2026):
- vaga em dólar não tem problema de senioridade, a não ser que a própria vaga
  exija (gestão de pessoas, cargo executivo, anos numa função que ele não tem).
  Por isso "entry level" no título NÃO descarta; só estágio/intern descarta;
- vaga presencial/híbrida no Brasil (ex.: SP) fica se for de empresa
  estrangeira/paga em dólar; senão sai (ver screen.collect_jobs).
"""
from __future__ import annotations

import re

from collectors._util import _LOCAL_BR, _LOCAL_FORTE, _fold, aceita_brasil

PERFIL = "intl"

TRILHAS_INTL = {
    "ecommerce_marketplace": "E-commerce, marketplace e DTC operations: sellers, catálogo, Amazon/Shopify, category",
    "gtm_revops": "GTM/Revenue/Sales Operations, GTM engineering, outbound operations, CRM e funil",
    "ai_automation": "Automação com IA aplicada a vendas/operações (Clay, n8n, Make, agentes, workflows)",
    "growth_ops": "Growth operations, marketing operations, lifecycle, performance",
    "product_ops": "Product Operations, Business Operations, Strategy & Operations",
    "outro": "Não se encaixa claramente em nenhuma trilha acima",
}

RECOMENDACOES_INTL = {
    "aplicar_agora": "Vale abordagem direta já: forte aderência, aceita Brasil, sem bloqueios",
    "avaliar": "Vale considerar com ressalvas: ler a vaga com calma antes de abordar",
    "baixa_prioridade": "Aderência fraca: só se faltarem opções melhores",
    "nao_aplicar": "Não vale o esforço: desalinhada, ou não aceita candidato no Brasil",
}

ELEGIBILIDADE = {
    "sim": "A vaga aceita candidato morando no Brasil (remota global, LATAM, Américas ou Brasil)",
    "incerto": "A vaga não deixa claro de onde aceita candidatos",
    "nao": "A vaga restringe a outro país/região (US only, EU only, autorização de trabalho, presencial fora do Brasil)",
}
ELEGIBILIDADE_PESO = {"sim": 1.0, "incerto": 0.5, "nao": 0.0}

INGLES = {
    "basico": "Inglês básico ou não mencionado",
    "intermediario": "Inglês intermediário / boa comunicação",
    "avancado": "Inglês avançado / fluente / excelente comunicação escrita e oral",
    "nativo": "Inglês nativo ou quase nativo exigido (native, near-native, first language)",
}

GAPS = {
    "nenhum": "Nenhum requisito obrigatório claramente não atendido",
    "espanhol": "Espanhol obrigatório",
    "ingles_nativo": "Inglês nativo/quase nativo obrigatório",
    "gestao_pessoas": "Gestão de pessoas / time direto obrigatória",
    "sql_python_avancado": "SQL ou Python avançado, ou engenharia de software, como núcleo da função",
    "salesforce_admin": "Salesforce admin/developer obrigatório",
    "autorizacao_trabalho": "Autorização de trabalho/residência em outro país obrigatória",
    "outro": "Outro requisito obrigatório não atendido",
}

TIPO_EMPRESA = {
    "agencia": "Agência (GTM, outbound, growth, marketplace/Amazon, automação/IA) ou staffing",
    "marca_dtc": "Marca DTC/e-commerce",
    "empresa_pequena": "Empresa pequena (até ~50 pessoas) ou startup inicial",
    "empresa_media": "Empresa média (~50 a 500 pessoas)",
    "marketplace": "Marketplace ou plataforma de e-commerce",
    "saas": "Empresa de software (SaaS) em crescimento",
    "grande_corporacao": "Grande corporação / multinacional",
}

# campos do Jev que ficam no historico_intl.json
CACHE_SCORE_FIELDS_INTL = [
    "nota_fit", "aderencia_nivel", "confianca", "senioridade_ok", "gap_critico", "gap_qual",
    "elegibilidade_jev", "ingles_exigido", "tipo_empresa", "trilha", "recomendacao",
]


def build_questions_intl() -> dict:
    """Rubrica do Perfil 2. Sempre com `preferencias` (o preferencias_intl.txt
    ou o bloco do config_intl.yaml)."""
    base = {"vaga": "`vaga`", "preferencias": "`preferencias`"}
    return {
        "aderencia": {
            "type": "score",
            "instructions": {
                **base,
                "question": "Considerando o currículo em `cv` e as preferências do candidato em "
                "`preferencias`, quão aderente esse candidato é aos requisitos e ao escopo real da "
                "vaga `vaga`? Se a função central da vaga for algo que `preferencias` diz que o "
                "candidato não quer ou não exerce, a aderência é baixa mesmo com pontos de contato. "
                "Ferramentas como Clay, Apollo, Smartlead, HubSpot e n8n são aprendíveis e não "
                "reduzem a aderência.",
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
                **base,
                "question": "A vaga `vaga` é de empresa estrangeira/paga em dólar, então o nível "
                "nominal (júnior, associate, entry-level, pleno, sênior, especialista) NÃO é problema. "
                "Considerando o `cv`, a vaga é compatível com o candidato em senioridade? Responda "
                "que NÃO apenas se a própria vaga exigir algo de senioridade que ele não tem: gestão "
                "de pessoas/time direto, cargo executivo (Head, VP, Director), ou muitos anos numa "
                "função específica que o `cv` não mostra.",
            },
            "criteria": {
                "true": "Compatível: a vaga não exige gestão de pessoas, cargo executivo nem anos que ele não tem",
                "false": "A vaga exige gestão de pessoas, cargo executivo ou anos numa função que ele não tem",
            },
        },
        "elegibilidade_brasil": {
            "type": "choice",
            "instructions": {
                **base,
                "question": "A vaga `vaga` aceita um candidato morando no Brasil, trabalhando remoto "
                "(ou presencial no Brasil)? Considere o local e a descrição: 'US only', 'must be "
                "authorized to work in the US', 'EU only', 'must reside in', presencial/híbrida fora "
                "do Brasil = não. Remote worldwide/anywhere/LATAM/Americas/Brasil = sim. Sem "
                "informação clara = incerto.",
            },
            "criteria": ELEGIBILIDADE,
        },
        "ingles_exigido": {
            "type": "choice",
            "instructions": {**base, "question": "Qual nível de inglês a vaga `vaga` exige?"},
            "criteria": INGLES,
        },
        "gap_critico": {
            "type": "noul",
            "instructions": {
                **base,
                "question": "Existe algum requisito OBRIGATÓRIO na vaga `vaga` que claramente não é "
                "coberto pelo currículo em `cv`? Contam: espanhol, inglês nativo, gestão de pessoas, "
                "SQL/Python avançado ou engenharia de software como núcleo, Salesforce admin, "
                "autorização de trabalho em outro país. Ferramentas aprendíveis (Clay, Apollo, "
                "Smartlead, HubSpot, n8n) e itens 'nice to have' NÃO contam.",
            },
        },
        "gap_qual": {
            "type": "choice",
            "instructions": {
                **base,
                "question": "Se a vaga `vaga` tem um requisito obrigatório que o `cv` não cobre, "
                "qual é o principal?",
            },
            "criteria": GAPS,
        },
        "tipo_empresa": {
            "type": "choice",
            "instructions": {**base, "question": "Que tipo de empresa está contratando na vaga `vaga`?"},
            "criteria": TIPO_EMPRESA,
        },
        "trilha": {
            "type": "choice",
            "instructions": {
                **base,
                "question": "Em qual trilha de atuação do candidato essa vaga `vaga` melhor se encaixa?",
            },
            "criteria": TRILHAS_INTL,
        },
        "recomendacao": {
            "type": "choice",
            "instructions": {
                **base,
                "question": "Dado o currículo em `cv`, as preferências em `preferencias` e a vaga "
                "`vaga`, vale uma abordagem direta (candidatura + mensagem no LinkedIn)? A "
                "recomendação final é:",
            },
            "criteria": RECOMENDACOES_INTL,
        },
    }


def notas_intl(answers: dict) -> dict:
    """Campos do relatório a partir das respostas do Jev. Pesos do Perfil 2:
    aderência 45%, senioridade 20%, elegibilidade 20%, ausência de gap 15%."""
    aderencia = answers["aderencia"]
    senioridade = answers["senioridade_compativel"]["noul"]
    gap = answers["gap_critico"]["noul"]
    eleg = answers["elegibilidade_brasil"]["choice"]
    n_levels = len(aderencia["legend"])
    fit = aderencia["score"] / max(n_levels - 1, 1)
    composite = (fit * 10 * 0.45 + senioridade * 10 * 0.20
                 + ELEGIBILIDADE_PESO.get(eleg, 0.5) * 10 * 0.20 + (1 - gap) * 10 * 0.15)
    tem_gap = gap >= 0.5
    return {
        "nota_fit": round(composite, 1),
        "aderencia_nivel": aderencia["legend"][str(int(aderencia["score"]))],
        "confianca": aderencia["confidence"],
        "senioridade_ok": "sim" if senioridade >= 0.5 else "não",
        "gap_critico": "sim" if tem_gap else "não",
        "gap_qual": answers["gap_qual"]["choice"] if tem_gap else "nenhum",
        "elegibilidade_jev": eleg,
        "ingles_exigido": answers["ingles_exigido"]["choice"],
        "tipo_empresa": answers["tipo_empresa"]["choice"],
        "trilha": answers["trilha"]["choice"],
        "recomendacao": answers["recomendacao"]["choice"],
    }


# ---------------------------------------------------------------------------
# Elegibilidade: a vaga aceita alguém morando no Brasil? (seção 6 do prompt)
# ---------------------------------------------------------------------------
_PAIS = (r"(the )?(us|u\.s\.?|usa|united states|uk|united kingdom|eu|european union|europe|canada|"
         r"australia|germany|india|north america)")
_RESTRITA_RE = re.compile(
    r"\b(us|u\.s\.|usa|eu|uk|canada|north america|united states|europe)[- ](only|based only)\b|"
    r"\b(only|exclusively) (open to|available to|hiring|accepting) (candidates|applicants|residents|people)?"
    r"\s*(who are |located |based |residing )?(in )?" + _PAIS + r"\b|"
    r"\bmust (be )?(be based|based|located|reside|live|currently reside|currently live|be a resident)"
    r" (in|within) " + _PAIS + r"\b|"
    r"\b(authori[sz]ed|authori[sz]ation|eligible|eligibility|right) to work (in|within) " + _PAIS + r"\b|"
    r"\bwork authori[sz]ation (in|for) " + _PAIS + r"\b|"
    r"\b(us|u\.s\.) (citizens?|citizenship|work authori[sz]ation|residents?) (only|required)\b|"
    r"\bvisa sponsorship is not available\b",
    re.IGNORECASE,
)
_MUST_RESIDE_RE = re.compile(r"\bmust (reside|live|be based|be located) in ([a-z ,]{2,40})", re.IGNORECASE)


def checar_elegibilidade(job: dict) -> tuple[str, str]:
    """("sim" | "nao" | "incerta", motivo). Olha o local e a descrição, não o
    país da empresa. O local citando Brasil/LATAM/Américas vence tudo; depois
    uma restrição explícita (US only, autorização de trabalho...) descarta;
    depois "worldwide/anywhere" sem restrição aceita; o resto é incerto."""
    local = f"{job.get('cidade') or ''} {job.get('estado') or ''}".strip()
    desc = job.get("descricao") or ""
    loc = _fold(local)
    if _LOCAL_FORTE.search(loc) or (not job.get("remoto") and _LOCAL_BR.search(loc)):
        return "sim", ""
    m = _RESTRITA_RE.search(_fold(desc)) or _RESTRITA_RE.search(loc)
    if m:
        return "nao", f"restrição na vaga: “{m.group(0).strip()}”"
    m = _MUST_RESIDE_RE.search(_fold(desc))
    if m and not re.search(r"brazil|brasil|latam|latin america|americas|anywhere|worldwide", m.group(2)):
        return "nao", f"restrição na vaga: “{m.group(0).strip()}”"
    if aceita_brasil(local, desc):
        return "sim", ""
    return "incerta", ""


# ---------------------------------------------------------------------------
# Regras determinísticas (seção 8 do prompt, com os ajustes do Michel)
# ---------------------------------------------------------------------------
_ESTAGIO_RE = re.compile(r"\b(intern|internship|estagio|estagiario|apprentice|apprenticeship)\b")
_GESTAO_RE = re.compile(
    r"\b(manage|managing|lead and manage|build and manage|hire and manage) (a|the|our|your) "
    r"(small |growing |high[- ]performing )?team\b|\bpeople management\b|\bdirect reports\b|"
    r"\bpeople manager\b|\bline management\b"
)
_NATIVO_RE = re.compile(
    r"\bnative[- ](level )?english\b|\bnear[- ]native\b|\benglish[^.;\n]{0,25}\bnative\b|"
    r"\bnative english speaker\b|\benglish as (a |your )?(first|native) language\b"
)
_SQL_PY_RE = re.compile(
    r"\b(advanced|expert|expert[- ]level|deep|strong) (proficiency (in|with) |knowledge of |skills in |)"
    r"(sql|python)\b|\b(sql|python) (expert|expertise|mastery)\b|\badvanced (sql|python) skills\b"
)
_FRASE_SPLIT_RE = re.compile(r"[.;\n•·|]|\s-\s")
_ESPANHOL_OBRIG_RE = re.compile(r"\b(fluent|fluency|native|required|must|mandatory|bilingual|proficien|advanced|"
                                r"professional working|full professional|c1|c2|essential)")
_ESPANHOL_OPC_RE = re.compile(r"\b(plus|nice to have|bonus|preferred|advantage|desirable|a big plus|not required|"
                              r"helpful|ideally)\b")


def exige_espanhol(texto: str) -> bool:
    """True se alguma frase cita espanhol como obrigatório ("fluent in Spanish",
    "Spanish required", "bilingual English/Spanish"); "Spanish is a plus" não conta."""
    for frase in _FRASE_SPLIT_RE.split(_fold(texto)):
        if re.search(r"\b(spanish|espanol|espanhol)\b", frase) and _ESPANHOL_OBRIG_RE.search(frase) \
                and not _ESPANHOL_OPC_RE.search(frase):
            return True
    return False


_ORDEM = ["aplicar_agora", "avaliar", "baixa_prioridade", "nao_aplicar"]


def _no_maximo(r: dict, teto: str, motivo: str) -> None:
    """Rebaixa a recomendação até `teto` (nunca sobe) e anota o motivo em `ajuste`."""
    atual = r.get("recomendacao")
    if atual in _ORDEM and _ORDEM.index(atual) < _ORDEM.index(teto):
        r["recomendacao"] = teto
        r["ajuste"] = f"{r['ajuste']} · {motivo}" if r.get("ajuste") else motivo


def ajustar_recomendacao_intl(r: dict) -> dict:
    """Regras do Perfil 2 por cima do Jev. Preenche `elegibilidade` e `ajuste`."""
    r["ajuste"] = r.get("ajuste") or ""
    r["perfil"] = PERFIL
    titulo = _fold(r.get("titulo") or "")
    desc = _fold(r.get("descricao") or "")

    eleg, motivo_eleg = checar_elegibilidade(r)
    if eleg == "incerta":
        # a descrição não diz; vale o que o Jev entendeu lendo a vaga
        eleg = {"sim": "sim", "nao": "nao"}.get(r.get("elegibilidade_jev"), "incerta")
        if eleg == "nao":
            motivo_eleg = "o Jev entende que a vaga não aceita candidato no Brasil"
    r["elegibilidade"] = eleg

    if _ESTAGIO_RE.search(titulo):
        _no_maximo(r, "nao_aplicar", "estágio/intern")
    if eleg == "nao":
        _no_maximo(r, "nao_aplicar", f"não aceita Brasil ({motivo_eleg})")
    if _GESTAO_RE.search(desc):
        _no_maximo(r, "nao_aplicar", "exige gestão de pessoas")
    if exige_espanhol(r.get("descricao") or ""):
        _no_maximo(r, "nao_aplicar", "exige espanhol")
    if _NATIVO_RE.search(desc) or r.get("ingles_exigido") == "nativo":
        _no_maximo(r, "avaliar", "pede inglês nativo/quase nativo")
    if _SQL_PY_RE.search(desc):
        _no_maximo(r, "avaliar", "SQL/Python avançado como requisito")
    if eleg == "incerta":
        _no_maximo(r, "avaliar", "elegibilidade incerta: a vaga não diz se aceita Brasil")
    if r.get("gap_critico") == "sim":
        _no_maximo(r, "avaliar", f"o Jev aponta requisito obrigatório não atendido ({r.get('gap_qual', '')})")
    return r

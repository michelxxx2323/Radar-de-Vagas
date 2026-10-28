"""
Estado de cada vaga que passou pelo kit: output/kits/candidaturas.json.

Gravado pelo kit.py (quando monta, pula ou deixa uma vaga para decidir) e
pelo painel.py (decisões, status, anotações). O registro.csv continua sendo o
log das decisões; este arquivo é o estado atual, uma entrada por vaga
(chave = link da vaga).

Status, na ordem do funil:
    decidir     falta requisito obrigatório e ninguém respondeu "montar mesmo assim?"
    pulada      decidiu não montar
    pronta      kit montado, falta revisar e enviar
    enviada     candidatura enviada (data em `enviada_em`)
    entrevista, oferta, recusada
    encerrada   desisti / vaga fechou
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import os
import threading
from pathlib import Path

STATUS = ["decidir", "pulada", "pronta", "enviada", "entrevista", "oferta", "recusada", "encerrada"]
# depois de enviada, refazer o kit não volta o status para "pronta"
POS_ENVIO = {"enviada", "entrevista", "oferta", "recusada", "encerrada"}
CAMPOS_VAGA = ["titulo", "empresa", "link", "fonte", "nota_fit", "recomendacao", "cidade", "estado",
               "remoto", "salario", "publicado_em"]

# o que a aba "Vagas de hoje" recebe de cada vaga do relatório
CAMPOS_LISTA = ["n", "nota_fit", "recomendacao", "titulo", "empresa", "fonte", "cidade", "remoto",
                "salario", "publicado_em", "link"]

_travas: dict[str, threading.Lock] = {}
_travas_lock = threading.Lock()


def chave(link: str) -> str:
    return hashlib.md5(link.encode()).hexdigest()[:12]


def _agora() -> str:
    # UTC, igual ao painel na nuvem (web/api/index.py): "qual versão é mais
    # nova" é comparado como texto, e a Vercel roda em UTC
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")


def em_utc(iso: str) -> str:
    """Horário sem fuso (gravado antes de 28/10/2026, no horário do PC) -> UTC."""
    if not iso:
        return iso
    t = dt.datetime.fromisoformat(iso)
    if t.tzinfo is not None:
        return iso  # já está como _agora() grava; reformatar mudaria a comparação
    return t.astimezone().astimezone(dt.timezone.utc).isoformat(timespec="milliseconds")


class Candidaturas:
    def __init__(self, path: Path):
        self.path = Path(path)
        with _travas_lock:
            self._trava = _travas.setdefault(str(self.path.resolve()), threading.Lock())

    def _ler(self) -> dict:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _gravar(self, dados: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)  # nunca deixa o arquivo pela metade

    def todas(self) -> list[dict]:
        with self._trava:
            return list(self._ler().values())

    def obter(self, id_: str) -> dict | None:
        with self._trava:
            return next((e for e in self._ler().values() if e["id"] == id_), None)

    def atualizar(self, vaga: dict, status: str | None = None, **campos) -> dict:
        """Cria ou atualiza a entrada da vaga. `status` novo entra no histórico."""
        if status is not None and status not in STATUS:
            raise ValueError(f"status desconhecido: {status}")
        with self._trava:
            dados = self._ler()
            link = vaga["link"]
            e = dados.get(link) or {"id": chave(link), "criado_em": _agora(), "historico": [], "notas": ""}
            e.update({k: vaga.get(k, e.get(k, "")) for k in CAMPOS_VAGA})
            e.update(campos)
            if status and status != e.get("status"):
                e["status"] = status
                e["historico"].append({"status": status, "em": _agora()})
                if status == "enviada" and not e.get("enviada_em"):
                    e["enviada_em"] = dt.date.today().isoformat()
            e["atualizado_em"] = _agora()
            dados[link] = e
            self._gravar(dados)
            return e

    def substituir(self, entradas: list[dict]) -> None:
        """Grava entradas inteiras como vieram (a versão da nuvem, na sincronização)."""
        with self._trava:
            dados = self._ler()
            for e in entradas:
                dados[e["link"]] = e
            self._gravar(dados)


def candidatura_sem_kit(dados: dict) -> tuple[dict, dict]:
    """Uma candidatura enviada sem kit (pelo botão "Já enviei" ou pelo
    formulário "Adicionar candidatura"): devolve (vaga, campos) para
    Candidaturas.atualizar(vaga, "enviada", **campos). Sem link (vaga que não
    veio do relatório), cria um identificador "manual:...". Mesma regra de
    web/api/index.py. ValueError se faltar vaga ou empresa, ou se a data não
    for AAAA-MM-DD."""
    import re
    import uuid

    titulo, empresa = (dados.get("titulo") or "").strip(), (dados.get("empresa") or "").strip()
    if not titulo or not empresa:
        raise ValueError("Preencha a vaga e a empresa.")
    enviada_em = (dados.get("enviada_em") or "").strip() or dt.date.today().isoformat()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", enviada_em):
        raise ValueError("Data de envio inválida.")
    # só os campos preenchidos: num registro que já existe, os vazios não apagam nada
    vaga = {k: dados[k] for k in CAMPOS_VAGA if dados.get(k) not in (None, "")}
    vaga.update(titulo=titulo, empresa=empresa, fonte=vaga.get("fonte") or "manual",
                link=(dados.get("link") or "").strip() or f"manual:{uuid.uuid4().hex[:12]}")
    campos = {"enviada_em": enviada_em}
    if (dados.get("notas") or "").strip():
        campos["notas"] = dados["notas"].strip()
    return vaga, campos


def vagas_para_lista(vagas: list[dict]) -> list[dict]:
    """Vagas do relatório que vão para a aba "Vagas de hoje" (aplicar agora e
    avaliar), só com os campos da lista — sem a descrição."""
    return [{**{k: v.get(k, "") for k in CAMPOS_LISTA}, "id": chave(v["link"])}
            for v in vagas if v.get("recomendacao") in ("aplicar_agora", "avaliar")]


def importar_existentes(kits_dir: Path) -> int:
    """Kits e decisões de antes deste arquivo existir: cada kit.json vira
    "pronta" e cada "pulada" do registro.csv vira "pulada". Devolve quantas
    entradas criou."""
    estado = Candidaturas(kits_dir / "candidaturas.json")
    conhecidos = {e["link"] for e in estado.todas()}
    criadas = 0
    for kit_json in sorted(kits_dir.glob("*/kit.json")):
        dados = json.loads(kit_json.read_text(encoding="utf-8"))
        vaga = dados["vaga"]
        if vaga["link"] in conhecidos:
            continue
        pdfs = sorted(kit_json.parent.glob("*.pdf"))
        estado.atualizar(vaga, "pronta", pasta=str(kit_json.parent), pdf=str(pdfs[0]) if pdfs else "",
                         lacunas=dados.get("obrigatorios_faltando", []),
                         alertas=alertas_do_kit(dados), gerado_em=dados.get("gerado_em", ""))
        conhecidos.add(vaga["link"])
        criadas += 1
    registro = kits_dir / "registro.csv"
    if registro.exists():
        with registro.open(encoding="utf-8-sig") as f:
            for linha in csv.DictReader(f):
                if linha["decisao"] != "pulada" or linha["link"] in conhecidos:
                    continue
                vaga = {"titulo": linha["vaga"], "empresa": linha["empresa"], "link": linha["link"],
                        "fonte": linha["fonte"], "nota_fit": linha["nota_jev"]}
                lacunas = [{"requisito": l.strip(), "motivo": ""} for l in linha["lacunas"].split(";") if l.strip()]
                estado.atualizar(vaga, "pulada", lacunas=lacunas)
                conhecidos.add(linha["link"])
                criadas += 1
    return criadas


def alertas_do_kit(dados: dict) -> dict:
    """O que o painel mostra como "antes de enviar", a partir do kit.json."""
    from kit import PAGINAS, marcadores

    paginas = dados.get("paginas")
    return {
        "paginas": paginas if paginas not in (None, PAGINAS) else None,
        "preencher": marcadores(dados["kit"]),
        "numeros": dados.get("numeros_a_conferir", []),
        "termos": dados.get("termos_proibidos", []),
        "pdf_na_pasta": bool(dados.get("pdf_copiado")),
    }

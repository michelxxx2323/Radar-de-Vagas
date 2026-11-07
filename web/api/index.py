"""
Painel de candidaturas (Vercel): as vagas do dia e o acompanhamento das
candidaturas enviadas, com os dados num banco (Postgres na Vercel; SQLite nos
testes).

O PC conversa com isto pelo sincronizar.py, uma vez por dia, depois da coleta:
sobe as vagas do relatório e guarda uma cópia das candidaturas. As
candidaturas em si são registradas aqui ("Já enviei" ou "+ Adicionar").

Variáveis de ambiente (Vercel > Settings > Environment Variables):
    DATABASE_URL    Postgres (a integração da Neon preenche sozinha)
    PAINEL_SENHA    senha para entrar pelo navegador
    PAINEL_TOKEN    token que o sincronizar.py usa (o mesmo no .env do PC)
    SEGREDO         chave que assina o cookie de login (qualquer texto longo)
Sem DATABASE_URL, usa o SQLite em SQLITE_PATH (padrão: painel_local.db).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import os
import re
import sqlite3
import uuid
from functools import wraps
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_file, session

PAGINA = Path(__file__).resolve().parent.parent / "index.html"
STATUS = ["enviada", "entrevista", "oferta", "recusada", "encerrada"]
# "Descartar" na aba Vagas de hoje (pedido do Michel, 06/11/2026): o motivo
# fica salvo pra análise depois (o sincronizar.py copia pra output/descartes.json)
MOTIVOS_DESCARTE = [
    "Vendas / meta individual",
    "Senioridade não bate",
    "Fora da minha área",
    "Presencial / local ruim",
    "Salário baixo",
    "Falta requisito importante",
    "Empresa não me interessa",
    "Vaga antiga ou fechada",
    "Outro",
]
# avaliação de cada termo da aba Lacunas: "tenho" (vai pra lista do CV) ou "lacuna"
AVALIACOES_LACUNA = ["tenho", "lacuna"]
CAMPOS_VAGA = ["titulo", "empresa", "link", "fonte", "nota_fit", "recomendacao", "cidade", "estado",
               "remoto", "salario", "publicado_em", "perfil"]

ESQUEMA = """
CREATE TABLE IF NOT EXISTS candidaturas (link TEXT PRIMARY KEY, id TEXT UNIQUE NOT NULL,
    dados TEXT NOT NULL, atualizado_em TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS vagas (link TEXT PRIMARY KEY, dados TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS meta (chave TEXT PRIMARY KEY, valor TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS descartes (link TEXT PRIMARY KEY, dados TEXT NOT NULL);
"""


def _agora() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")


def _hoje() -> str:
    # a Vercel roda em UTC; "enviada hoje" é o dia em São Paulo
    from zoneinfo import ZoneInfo

    return dt.datetime.now(ZoneInfo("America/Sao_Paulo")).date().isoformat()


def _chave(link: str) -> str:
    return hashlib.md5(link.encode()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Banco: Postgres (psycopg) ou SQLite, com o mesmo SQL (placeholders %s)
# ---------------------------------------------------------------------------

class Banco:
    def __init__(self):
        self.url = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL")
        self._pronto = False

    def conectar(self):
        if self.url:
            import psycopg

            return psycopg.connect(self.url)
        return sqlite3.connect(os.environ.get("SQLITE_PATH", "painel_local.db"))

    def _sql(self, sql: str) -> str:
        return sql if self.url else sql.replace("%s", "?")

    def _criar(self, conn) -> None:
        cur = conn.cursor()
        for comando in filter(str.strip, ESQUEMA.split(";")):
            cur.execute(comando)
        conn.commit()
        self._pronto = True

    def executar(self, sql: str, params: tuple = (), *, um=False, todos=False):
        conn = self.conectar()
        try:
            if not self._pronto:
                self._criar(conn)
            cur = conn.cursor()
            cur.execute(self._sql(sql), params)
            res = cur.fetchone() if um else cur.fetchall() if todos else None
            conn.commit()
            return res
        finally:
            conn.close()

    def substituir_vagas(self, vagas: list[dict]) -> None:
        """Troca a lista de vagas do dia numa transação só. executemany manda as
        ~1.200 linhas de uma vez (no Postgres, em pipeline) em vez de uma ida e
        volta ao banco por vaga."""
        conn = self.conectar()
        try:
            if not self._pronto:
                self._criar(conn)
            cur = conn.cursor()
            cur.execute("DELETE FROM vagas")
            cur.executemany(self._sql("INSERT INTO vagas (link, dados) VALUES (%s, %s) ON CONFLICT (link) DO NOTHING"),
                            [(v["link"], json.dumps(v, ensure_ascii=False)) for v in vagas])
            conn.commit()
        finally:
            conn.close()

    def candidaturas(self) -> list[dict]:
        return [json.loads(d) for (d,) in self.executar("SELECT dados FROM candidaturas", todos=True)]

    def candidatura(self, id_: str) -> dict | None:
        linha = self.executar("SELECT dados FROM candidaturas WHERE id = %s", (id_,), um=True)
        return json.loads(linha[0]) if linha else None

    def mudar(self, e: dict, status: str | None = None, **campos) -> dict:
        """Grava a candidatura; um status novo entra no histórico."""
        if status is not None and status not in STATUS:
            abort(400, f"status desconhecido: {status}")
        e.update(campos)
        if status and status != e.get("status"):
            e["status"] = status
            e.setdefault("historico", []).append({"status": status, "em": _agora()})
            if status == "enviada" and not e.get("enviada_em"):
                e["enviada_em"] = _hoje()
        e["atualizado_em"] = _agora()
        self.executar(
            "INSERT INTO candidaturas (link, id, dados, atualizado_em) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (link) DO UPDATE SET dados = excluded.dados, atualizado_em = excluded.atualizado_em",
            (e["link"], e["id"], json.dumps(e, ensure_ascii=False), e["atualizado_em"]))
        return e

    def descartes(self) -> list[dict]:
        return [json.loads(d) for (d,) in self.executar("SELECT dados FROM descartes", todos=True)]

    def meta_json(self, chave: str) -> dict:
        return json.loads(self.meta(chave) or "{}")

    def meta(self, chave: str) -> str:
        linha = self.executar("SELECT valor FROM meta WHERE chave = %s", (chave,), um=True)
        return linha[0] if linha else ""

    def gravar_meta(self, chave: str, valor: str) -> None:
        self.executar("INSERT INTO meta (chave, valor) VALUES (%s, %s) "
                      "ON CONFLICT (chave) DO UPDATE SET valor = excluded.valor", (chave, valor))


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

def criar_app(banco: Banco | None = None) -> Flask:
    banco = banco or Banco()
    app = Flask(__name__, static_folder=None)
    app.secret_key = os.environ.get("SEGREDO") or os.urandom(32)
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                      SESSION_COOKIE_SECURE=bool(os.environ.get("VERCEL")),
                      PERMANENT_SESSION_LIFETIME=dt.timedelta(days=60))

    def _igual(a: str, b: str) -> bool:
        return bool(a) and bool(b) and hmac.compare_digest(a.encode(), b.encode())

    def navegador(f):
        @wraps(f)
        def dentro(*a, **k):
            if not session.get("ok"):
                abort(401, "Entre com a senha do painel.")
            return f(*a, **k)
        return dentro

    def pc(f):
        @wraps(f)
        def dentro(*a, **k):
            token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
            if not _igual(token, os.environ.get("PAINEL_TOKEN", "")):
                abort(401, "Token do PC inválido.")
            return f(*a, **k)
        return dentro

    def entrada(id_: str) -> dict:
        e = banco.candidatura(id_)
        if not e:
            abort(404, "Candidatura não encontrada.")
        return e

    # --- página e login ---

    @app.get("/")
    def index():
        return send_file(PAGINA)

    @app.post("/api/login")
    def login():
        if not _igual((request.json or {}).get("senha", ""), os.environ.get("PAINEL_SENHA", "")):
            abort(401, "Senha errada.")
        session.permanent = True
        session["ok"] = True
        return jsonify(ok=True)

    @app.post("/api/sair")
    def sair():
        session.clear()
        return jsonify(ok=True)

    # --- o que a página usa ---

    @app.get("/api/painel")
    @navegador
    def painel():
        cands = banco.candidaturas()
        status = {c["link"]: c.get("status", "") for c in cands}
        descartes = banco.descartes()
        descartada = {d["link"]: d.get("motivo", "") for d in descartes}
        vagas = []
        for (d,) in banco.executar("SELECT dados FROM vagas", todos=True):
            v = json.loads(d)
            v["status"] = status.get(v["link"], "")
            v["descartada"] = descartada.get(v["link"], "")
            vagas.append(v)
        vagas.sort(key=lambda v: v.get("n") or 0)
        return jsonify(vagas=vagas, candidaturas=cands, relatorio=banco.meta("relatorio"),
                       pc_visto_em=banco.meta("pc_visto_em"), lacunas=banco.meta_json("lacunas"),
                       lacunas_revisao=banco.meta_json("lacunas_revisao"), descartes=descartes,
                       coletores=banco.meta_json("coletores"),
                       motivos_descarte=MOTIVOS_DESCARTE)

    @app.post("/api/candidatura")
    @navegador
    def candidatura():
        """Candidatura enviada: "Já enviei" numa vaga do dia ou o formulário
        "+ Adicionar" (vaga de fora do relatório, sem link obrigatório)."""
        d = request.json or {}
        titulo, empresa = (d.get("titulo") or "").strip(), (d.get("empresa") or "").strip()
        if not titulo or not empresa:
            abort(400, "Preencha a vaga e a empresa.")
        enviada_em = (d.get("enviada_em") or "").strip() or _hoje()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", enviada_em):
            abort(400, "Data de envio inválida.")
        link = (d.get("link") or "").strip() or f"manual:{uuid.uuid4().hex[:12]}"
        linha = banco.executar("SELECT dados FROM candidaturas WHERE link = %s", (link,), um=True)
        e = json.loads(linha[0]) if linha else {"id": _chave(link), "link": link, "criado_em": _agora(),
                                                "historico": [], "notas": ""}
        # só os campos preenchidos: num registro que já existe, os vazios não apagam nada
        e.update({k: d[k] for k in CAMPOS_VAGA if d.get(k) not in (None, "")})
        e.update(titulo=titulo, empresa=empresa, fonte=e.get("fonte") or "manual")
        campos = {"enviada_em": enviada_em}
        if (d.get("notas") or "").strip():
            campos["notas"] = d["notas"].strip()
        banco.mudar(e, "enviada", **campos)
        return jsonify(ok=True)

    @app.post("/api/status")
    @navegador
    def status():
        banco.mudar(entrada(request.json["id"]), request.json["status"])
        return jsonify(ok=True)

    @app.post("/api/notas")
    @navegador
    def notas():
        banco.mudar(entrada(request.json["id"]), notas=request.json["notas"])
        return jsonify(ok=True)

    @app.post("/api/followup")
    @navegador
    def followup():
        """Follow-up enviado (ideia do /outcome followup do ai-job-search): guarda
        a data em `followups`. O painel sugere o próximo depois de 10 dias de
        silêncio, no máximo dois por candidatura."""
        e = entrada(request.json["id"])
        if e.get("status") != "enviada":
            abort(400, "Follow-up só para candidatura com status Enviada.")
        if len(e.get("followups") or []) >= 2:
            abort(400, "Já foram dois follow-ups nesta candidatura.")
        banco.mudar(e, followups=[*(e.get("followups") or []), _hoje()])
        return jsonify(ok=True)

    @app.post("/api/descartar")
    @navegador
    def descartar():
        """Vaga do dia descartada, com o motivo. Guarda uma cópia dos dados da
        vaga, porque a lista de vagas do dia é trocada a cada rodada."""
        d = request.json or {}
        link, motivo = (d.get("link") or "").strip(), (d.get("motivo") or "").strip()
        if motivo not in MOTIVOS_DESCARTE:
            abort(400, "Escolha um motivo.")
        linha = banco.executar("SELECT dados FROM vagas WHERE link = %s", (link,), um=True)
        if not linha:
            abort(404, "Vaga não encontrada na lista de hoje.")
        v = json.loads(linha[0])
        reg = {k: v.get(k, "") for k in ["link", "titulo", "empresa", "fonte", "nota_fit", "recomendacao",
                                           "trilha", "cidade", "remoto", "salario", "publicado_em", "perfil"]}
        reg.update(motivo=motivo, nota=(d.get("nota") or "").strip()[:500], em=_agora())
        banco.executar("INSERT INTO descartes (link, dados) VALUES (%s, %s) "
                       "ON CONFLICT (link) DO UPDATE SET dados = excluded.dados",
                       (link, json.dumps(reg, ensure_ascii=False)))
        return jsonify(ok=True)

    @app.post("/api/desfazer-descarte")
    @navegador
    def desfazer_descarte():
        banco.executar("DELETE FROM descartes WHERE link = %s", ((request.json or {}).get("link", ""),))
        return jsonify(ok=True)

    @app.post("/api/lacuna")
    @navegador
    def avaliar_lacuna():
        """Sua avaliação de um termo da aba Lacunas: "tenho" (com uma nota de
        onde usou, pra ir pro CV), "lacuna" (é lacuna mesmo) ou "" (desfaz)."""
        d = request.json or {}
        termo, avaliacao = (d.get("termo") or "").strip(), d.get("avaliacao") or ""
        if not termo or avaliacao not in ["", *AVALIACOES_LACUNA]:
            abort(400, "Avaliação inválida.")
        rev = banco.meta_json("lacunas_revisao")
        if avaliacao:
            rev[termo] = {"avaliacao": avaliacao, "nota": (d.get("nota") or "").strip()[:500], "em": _agora()}
        else:
            rev.pop(termo, None)
        banco.gravar_meta("lacunas_revisao", json.dumps(rev, ensure_ascii=False))
        return jsonify(ok=True)

    @app.post("/api/excluir")
    @navegador
    def excluir():
        """Para uma candidatura registrada por engano."""
        banco.executar("DELETE FROM candidaturas WHERE id = %s", (entrada(request.json["id"])["id"],))
        return jsonify(ok=True)

    # --- o que o sincronizar.py usa ---

    @app.get("/api/pc/estado")
    @pc
    def pc_estado():
        banco.gravar_meta("pc_visto_em", _agora())
        return jsonify(candidaturas=banco.candidaturas(), relatorio=banco.meta("relatorio"),
                       assinatura=banco.meta("assinatura"), descartes=banco.descartes(),
                       lacunas_revisao=banco.meta_json("lacunas_revisao"))

    @app.put("/api/pc/vagas")
    @pc
    def pc_vagas():
        corpo = request.json
        banco.substituir_vagas(corpo["vagas"])
        banco.gravar_meta("relatorio", corpo["relatorio"])
        banco.gravar_meta("assinatura", corpo.get("assinatura", ""))
        if corpo.get("lacunas"):  # vazio = o PC não conseguiu calcular; fica o anterior
            banco.gravar_meta("lacunas", json.dumps(corpo["lacunas"], ensure_ascii=False))
        if corpo.get("coletores"):  # aba "Coletores": a situação de cada fonte na última rodada de cada perfil
            banco.gravar_meta("coletores", json.dumps(corpo["coletores"], ensure_ascii=False))
        return jsonify(ok=True)

    @app.errorhandler(400)
    @app.errorhandler(401)
    @app.errorhandler(404)
    def erro(e):
        return jsonify(erro=e.description), e.code

    return app


app = criar_app()

"""
Painel de candidaturas na nuvem (Vercel) — a mesma página do painel local,
com os dados num banco (Postgres na Vercel; SQLite nos testes).

O PC continua fazendo o trabalho pesado (coleta, kits, Word, OneDrive) e
conversa com isto pelo sincronizar.py:
- sobe vagas do dia, candidaturas, kits e PDFs;
- baixa os pedidos feitos aqui ("gerar kit", "montar mesmo assim", "refazer")
  e as mudanças de status/anotações.

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

from flask import Flask, Response, abort, jsonify, request, send_file, session

PAGINA = Path(__file__).resolve().parent.parent / "index.html"
STATUS = ["decidir", "pulada", "pronta", "enviada", "entrevista", "oferta", "recusada", "encerrada"]
# igual a candidaturas.CAMPOS_VAGA no PC
CAMPOS_VAGA = ["titulo", "empresa", "link", "fonte", "nota_fit", "recomendacao", "cidade", "estado",
               "remoto", "salario", "publicado_em"]
TIPOS_PEDIDO = {"gerar", "montar", "refazer"}

ESQUEMA = """
CREATE TABLE IF NOT EXISTS candidaturas (link TEXT PRIMARY KEY, id TEXT UNIQUE NOT NULL,
    dados TEXT NOT NULL, atualizado_em TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS kits (id TEXT PRIMARY KEY, dados TEXT NOT NULL, pdf {blob},
    gerado_em TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS vagas (link TEXT PRIMARY KEY, dados TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS meta (chave TEXT PRIMARY KEY, valor TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pedidos (n {serial}, tipo TEXT NOT NULL, link TEXT NOT NULL,
    criado_em TEXT NOT NULL, feito_em TEXT, erro TEXT);
"""


def _agora() -> str:
    # sempre em UTC, como no PC (candidaturas.py): a comparação de "qual versão
    # é mais nova" é por texto, e a Vercel roda em UTC
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")


def _hoje() -> str:
    from zoneinfo import ZoneInfo

    return dt.datetime.now(ZoneInfo("America/Sao_Paulo")).date().isoformat()


def _chave(link: str) -> str:
    # igual a candidaturas.chave() no PC
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
        conn = sqlite3.connect(os.environ.get("SQLITE_PATH", "painel_local.db"))
        return conn

    def _sql(self, sql: str) -> str:
        return sql if self.url else sql.replace("%s", "?")

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

    def _criar(self, conn) -> None:
        esquema = ESQUEMA.format(blob="BYTEA" if self.url else "BLOB",
                                 serial="SERIAL PRIMARY KEY" if self.url else "INTEGER PRIMARY KEY AUTOINCREMENT")
        cur = conn.cursor()
        for comando in filter(str.strip, esquema.split(";")):
            cur.execute(comando)
        conn.commit()
        self._pronto = True

    # --- candidaturas ---

    def candidaturas(self) -> list[dict]:
        return [json.loads(d) for (d,) in self.executar("SELECT dados FROM candidaturas", todos=True)]

    def candidatura(self, id_: str) -> dict | None:
        linha = self.executar("SELECT dados FROM candidaturas WHERE id = %s", (id_,), um=True)
        return json.loads(linha[0]) if linha else None

    def gravar_candidatura(self, e: dict, so_se_mais_nova: bool = False) -> bool:
        """Grava a entrada. Com `so_se_mais_nova`, não sobrescreve uma versão
        mais recente (o PC subindo algo que você já mudou aqui)."""
        if so_se_mais_nova:
            atual = self.executar("SELECT atualizado_em FROM candidaturas WHERE link = %s", (e["link"],), um=True)
            if atual and atual[0] > e["atualizado_em"]:
                return False
        self.executar(
            "INSERT INTO candidaturas (link, id, dados, atualizado_em) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (link) DO UPDATE SET dados = excluded.dados, atualizado_em = excluded.atualizado_em",
            (e["link"], e["id"], json.dumps(e, ensure_ascii=False), e["atualizado_em"]))
        return True

    def mudar(self, e: dict, status: str | None = None, **campos) -> dict:
        """Mesma regra do Candidaturas.atualizar() do PC: status novo entra no histórico."""
        if status is not None and status not in STATUS:
            abort(400, f"status desconhecido: {status}")
        e.update(campos)
        if status and status != e.get("status"):
            e["status"] = status
            e.setdefault("historico", []).append({"status": status, "em": _agora()})
            if status == "enviada" and not e.get("enviada_em"):
                e["enviada_em"] = _hoje()
        e["atualizado_em"] = _agora()
        self.gravar_candidatura(e)
        return e

    # --- pedidos para o PC ---

    def pedidos_pendentes(self) -> list[dict]:
        linhas = self.executar("SELECT n, tipo, link, criado_em FROM pedidos WHERE feito_em IS NULL ORDER BY n",
                               todos=True)
        return [{"n": n, "tipo": t, "link": l, "criado_em": c} for n, t, l, c in linhas]

    def pedir(self, tipo: str, link: str) -> None:
        if any(p["link"] == link for p in self.pedidos_pendentes()):
            return
        self.executar("INSERT INTO pedidos (tipo, link, criado_em) VALUES (%s, %s, %s)", (tipo, link, _agora()))

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

    # --- o que a página usa (mesmas rotas do painel local) ---

    @app.get("/api/painel")
    @navegador
    def painel():
        pendentes = {p["link"] for p in banco.pedidos_pendentes()}
        cands = banco.candidaturas()
        status = {c["link"]: c.get("status", "") for c in cands}
        vagas = []
        for (d,) in banco.executar("SELECT dados FROM vagas", todos=True):
            v = json.loads(d)
            v.update(status=status.get(v["link"], ""), gerando=v["link"] in pendentes)
            vagas.append(v)
        vagas.sort(key=lambda v: v.get("n") or 0)
        for c in cands:
            c.pop("descricao", None)
            c["gerando"] = c["link"] in pendentes
        return jsonify(vagas=vagas, candidaturas=cands, fila=len(pendentes), relatorio=banco.meta("relatorio"),
                       modo="nuvem", pc_visto_em=banco.meta("pc_visto_em"))

    @app.post("/api/gerar")
    @navegador
    def gerar():
        link = request.json["link"]
        if not banco.executar("SELECT 1 FROM vagas WHERE link = %s", (link,), um=True):
            abort(404, "Essa vaga não está no relatório de hoje.")
        banco.pedir("gerar", link)
        return jsonify(ok=True)

    @app.post("/api/decidir")
    @navegador
    def decidir():
        e = entrada(request.json["id"])
        if request.json["montar"]:
            banco.pedir("montar", e["link"])
        else:
            banco.mudar(e, "pulada")
        return jsonify(ok=True)

    @app.post("/api/refazer")
    @navegador
    def refazer():
        banco.pedir("refazer", entrada(request.json["id"])["link"])
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

    @app.post("/api/candidatura")
    @navegador
    def candidatura():
        """Candidatura enviada sem kit: "Já enviei" numa vaga ou o formulário.
        Mesma regra de candidaturas.candidatura_sem_kit() no PC."""
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

    @app.get("/api/kit/<id_>")
    @navegador
    def kit(id_):
        linha = banco.executar("SELECT dados FROM kits WHERE id = %s", (id_,), um=True)
        if not linha:
            abort(404, "O kit ainda não subiu do PC.")
        return Response(linha[0], mimetype="application/json")

    @app.get("/pdf/<id_>")
    @navegador
    def pdf(id_):
        linha = banco.executar("SELECT pdf FROM kits WHERE id = %s", (id_,), um=True)
        if not linha or not linha[0]:
            abort(404, "PDF não encontrado.")
        return Response(bytes(linha[0]), mimetype="application/pdf")

    # --- o que o sincronizar.py usa ---

    @app.get("/api/pc/estado")
    @pc
    def pc_estado():
        banco.gravar_meta("pc_visto_em", _agora())
        kits = {i: g for i, g in banco.executar("SELECT id, gerado_em FROM kits", todos=True)}
        return jsonify(candidaturas=banco.candidaturas(), pedidos=banco.pedidos_pendentes(), kits=kits,
                       relatorio=banco.meta("relatorio"))

    @app.put("/api/pc/candidaturas")
    @pc
    def pc_candidaturas():
        gravadas = sum(banco.gravar_candidatura(e, so_se_mais_nova=True) for e in request.json)
        return jsonify(gravadas=gravadas)

    @app.put("/api/pc/kit/<id_>")
    @pc
    def pc_kit(id_):
        dados = request.form["dados"]
        pdf = request.files["pdf"].read() if "pdf" in request.files else None
        banco.executar(
            "INSERT INTO kits (id, dados, pdf, gerado_em) VALUES (%s, %s, %s, %s) ON CONFLICT (id) DO UPDATE "
            "SET dados = excluded.dados, pdf = excluded.pdf, gerado_em = excluded.gerado_em",
            (id_, dados, pdf, request.form["gerado_em"]))
        return jsonify(ok=True)

    @app.put("/api/pc/vagas")
    @pc
    def pc_vagas():
        corpo = request.json
        banco.substituir_vagas(corpo["vagas"])
        banco.gravar_meta("relatorio", corpo["relatorio"])
        return jsonify(ok=True)

    @app.post("/api/pc/pedidos/<int:n>")
    @pc
    def pc_pedido_feito(n):
        banco.executar("UPDATE pedidos SET feito_em = %s, erro = %s WHERE n = %s",
                       (_agora(), (request.json or {}).get("erro", ""), n))
        return jsonify(ok=True)

    @app.errorhandler(400)
    @app.errorhandler(401)
    @app.errorhandler(404)
    def erro(e):
        return jsonify(erro=e.description), e.code

    return app


app = criar_app()

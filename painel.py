#!/usr/bin/env python3
"""
Painel das candidaturas — uma página no navegador para:

- decidir as vagas em que falta requisito obrigatório ("montar mesmo assim" / "pular");
- revisar os kits prontos (PDF, carta e respostas com botão de copiar, checklist);
- acompanhar as candidaturas enviadas (enviada → entrevista → oferta / recusada);
- gerar kits a partir das vagas do relatório de hoje.

Roda só no seu computador (http://127.0.0.1:8765), porque usa o Word, a pasta
do OneDrive e a chave da API daqui. O estado fica em output/kits/candidaturas.json
(ver candidaturas.py), o mesmo que o kit.py usa no terminal.

Uso:
    python painel.py            # abre o navegador sozinho
    python painel.py --mock     # kits simulados, sem API (pasta output/kits_mock)
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import threading
import traceback
import webbrowser
from pathlib import Path

import yaml
from flask import Flask, abort, jsonify, request, send_file

import kit as kitmod
from candidaturas import CAMPOS_VAGA, Candidaturas, candidatura_sem_kit, importar_existentes, vagas_para_lista

PORTA = 8765
PAGINA = Path(__file__).with_name("web") / "index.html"  # a mesma página do painel na nuvem


class Painel:
    def __init__(self, config_path: str, mock: bool):
        self.config = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
        self.mock = mock
        self.out_dir = Path(self.config.get("output_dir", "output"))
        self.kits_dir = self.out_dir / ("kits_mock" if mock else "kits")
        self.estado = Candidaturas(self.kits_dir / "candidaturas.json")
        self.fila: queue.Queue = queue.Queue()
        self.gerando: set[str] = set()  # links na fila ou em andamento
        self._vagas_cache: tuple[float, list[dict]] | None = None
        threading.Thread(target=self._trabalhador, daemon=True).start()

    # --- vagas do relatório ------------------------------------------------

    def vagas(self) -> list[dict]:
        """Último relatório com descrições; relido só quando o arquivo muda."""
        try:
            mtime = kitmod.ultimo_relatorio(self.out_dir).stat().st_mtime
        except SystemExit:
            return []
        if not self._vagas_cache or self._vagas_cache[0] != mtime:
            self._vagas_cache = (mtime, kitmod.carregar_vagas(self.out_dir))
        return self._vagas_cache[1]

    def vaga_por_link(self, link: str) -> dict | None:
        return next((v for v in self.vagas() if v.get("link") == link), None)

    # --- geração de kits (uma por vez, numa thread só) ---------------------

    def enfileirar(self, vaga: dict, refazer: bool = False, faltando: list[dict] | None = None) -> None:
        if vaga["link"] in self.gerando:
            return
        self.gerando.add(vaga["link"])
        self.fila.put((vaga, refazer, faltando))

    def _trabalhador(self) -> None:
        while True:
            vaga, refazer, faltando = self.fila.get()
            try:
                self._gerar(vaga, refazer, faltando)
            except Exception as e:  # noqa: BLE001 — o erro vai pro painel, a thread segue viva
                traceback.print_exc()
                self.estado.atualizar(vaga, erro=str(e))
            finally:
                self.gerando.discard(vaga["link"])

    def _gerar(self, vaga: dict, refazer: bool, faltando: list[dict] | None) -> None:
        self.estado.atualizar(vaga, erro="")
        kitmod.gerar_sem_terminal(self.config, vaga, self.kits_dir, mock=self.mock, refazer=refazer,
                                  faltando=faltando)

    # --- o que a página recebe ---------------------------------------------

    def resumo(self) -> dict:
        por_link = {e["link"]: e for e in self.estado.todas()}
        vagas = vagas_para_lista(self.vagas())
        for item in vagas:
            item["status"] = por_link.get(item["link"], {}).get("status", "")
            item["gerando"] = item["link"] in self.gerando
        candidaturas = []
        for e in por_link.values():
            leve = {k: val for k, val in e.items() if k != "descricao"}
            leve["gerando"] = e["link"] in self.gerando
            candidaturas.append(leve)
        try:
            relatorio = kitmod.ultimo_relatorio(self.out_dir).name
        except SystemExit:
            relatorio = ""
        return {"vagas": vagas, "candidaturas": candidaturas, "fila": len(self.gerando),
                "relatorio": relatorio, "mock": self.mock, "modo": "local"}


def criar_app(painel: Painel) -> Flask:
    app = Flask(__name__, static_folder=None)

    def entrada(id_: str) -> dict:
        e = painel.estado.obter(id_)
        if not e:
            abort(404)
        return e

    def vaga_completa(e: dict) -> dict:
        """A vaga como o kit.py precisa (com descrição): do relatório, ou a
        guardada no estado quando ela já saiu do relatório."""
        v = painel.vaga_por_link(e["link"])
        if v:
            return v
        if not e.get("descricao"):
            abort(409, "Essa vaga não está no relatório de hoje e não tenho a descrição dela guardada.")
        return {**{k: e.get(k, "") for k in CAMPOS_VAGA}, "descricao": e["descricao"]}

    @app.get("/")
    def index():
        return send_file(PAGINA)

    @app.get("/api/painel")
    def api_painel():
        return jsonify(painel.resumo())

    @app.post("/api/gerar")
    def api_gerar():
        link = request.json["link"]
        v = painel.vaga_por_link(link)
        if not v:
            abort(404, "Essa vaga não está no relatório de hoje.")
        painel.enfileirar(v)
        return jsonify(ok=True)

    @app.post("/api/decidir")
    def api_decidir():
        e = entrada(request.json["id"])
        if request.json["montar"]:
            painel.enfileirar(vaga_completa(e), refazer=True, faltando=e.get("lacunas") or [])
        else:
            kitmod.registrar(painel.kits_dir, e, e.get("lacunas") or [], "pulada")
            painel.estado.atualizar(e, "pulada")
        return jsonify(ok=True)

    @app.post("/api/refazer")
    def api_refazer():
        e = entrada(request.json["id"])
        painel.enfileirar(vaga_completa(e), refazer=True, faltando=e.get("lacunas") or [])
        return jsonify(ok=True)

    @app.post("/api/status")
    def api_status():
        e = entrada(request.json["id"])
        painel.estado.atualizar(e, request.json["status"])
        return jsonify(ok=True)

    @app.post("/api/notas")
    def api_notas():
        e = entrada(request.json["id"])
        painel.estado.atualizar(e, notas=request.json["notas"])
        return jsonify(ok=True)

    @app.post("/api/candidatura")
    def api_candidatura():
        """Candidatura enviada sem kit: "Já enviei" numa vaga ou o formulário."""
        try:
            vaga, campos = candidatura_sem_kit(request.json or {})
        except ValueError as e:
            abort(400, str(e))
        painel.estado.atualizar(vaga, "enviada", **campos)
        return jsonify(ok=True)

    @app.get("/api/kit/<id_>")
    def api_kit(id_):
        e = entrada(id_)
        kit_json = Path(e.get("pasta", "")) / "kit.json"
        if not e.get("pasta") or not kit_json.exists():
            abort(404, "Kit não encontrado.")
        dados = json.loads(kit_json.read_text(encoding="utf-8"))
        return jsonify({"kit": dados["kit"], "gerado_em": dados.get("gerado_em"),
                        "pdf_copiado": dados.get("pdf_copiado", "")})

    @app.get("/pdf/<id_>")
    def pdf(id_):
        e = entrada(id_)
        if not e.get("pdf") or not Path(e["pdf"]).exists():
            abort(404, "PDF não encontrado.")
        return send_file(Path(e["pdf"]).resolve(), mimetype="application/pdf")

    @app.post("/api/abrir")
    def api_abrir():
        """Abre a pasta do kit no Explorer (só existe porque o painel é local)."""
        e = entrada(request.json["id"])
        alvo = Path(e.get("pasta", ""))
        if not alvo.is_dir():
            abort(404)
        os.startfile(alvo)  # noqa: S606 — caminho vem do próprio estado, não da página
        return jsonify(ok=True)

    @app.errorhandler(400)
    @app.errorhandler(404)
    @app.errorhandler(409)
    def erro(e):
        return jsonify(erro=e.description), e.code

    return app


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--mock", action="store_true", help="kits simulados, sem chamar a API")
    ap.add_argument("--porta", type=int, default=PORTA)
    ap.add_argument("--sem-navegador", action="store_true", help="não abre o navegador sozinho")
    args = ap.parse_args()

    painel = Painel(args.config, args.mock)
    novas = importar_existentes(painel.kits_dir)
    if novas:
        print(f"  {novas} kit(s)/decisão(ões) anteriores importados para o painel.")
    url = f"http://127.0.0.1:{args.porta}"
    print(f"Painel em {url}  (Ctrl+C para fechar)")
    if not args.sem_navegador:
        threading.Timer(1.0, webbrowser.open, [url]).start()
    # 127.0.0.1: só este computador acessa
    criar_app(painel).run(host="127.0.0.1", port=args.porta, debug=False, threaded=True, load_dotenv=False)


if __name__ == "__main__":
    main()

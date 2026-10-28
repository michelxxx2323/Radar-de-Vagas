#!/usr/bin/env python3
"""
Sincroniza o PC com o painel na nuvem (pasta web/, publicada na Vercel).

O PC continua fazendo o trabalho pesado; a nuvem guarda e mostra. A cada rodada:

1. baixa as candidaturas da nuvem e fica com a versão mais nova de cada uma
   (status e anotações que você mudou pelo celular chegam aqui);
2. executa os pedidos feitos pelo painel na nuvem ("gerar kit", "montar
   mesmo assim", "refazer") — com o Word e a pasta do OneDrive daqui;
3. sobe o que mudou no PC: candidaturas, kits com PDF e as vagas do relatório
   do dia.

Uso:
    python sincronizar.py              # uma rodada (o rodar_diario.bat chama depois da coleta)
    python sincronizar.py --continuo   # uma rodada por minuto, para os pedidos do celular
                                       # (instalar_sincronizacao.bat deixa isso rodando escondido)

Precisa de PAINEL_URL (ex.: https://radar-de-vagas.vercel.app) e PAINEL_TOKEN
no .env — o token é o mesmo cadastrado na Vercel.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import sys
import time
import traceback
from pathlib import Path

import requests
import yaml

import kit as kitmod
from candidaturas import CAMPOS_VAGA, Candidaturas, em_utc, vagas_para_lista
from novidades import _ler_env

INTERVALO_S = 60


class Nuvem:
    """As rotas /api/pc/* do web/api/index.py."""

    def __init__(self, url: str, token: str):
        self.url = url.rstrip("/")
        self.sessao = requests.Session()
        self.sessao.headers["Authorization"] = f"Bearer {token}"

    def _req(self, metodo: str, caminho: str, **kw) -> dict:
        r = self.sessao.request(metodo, self.url + caminho, timeout=60, **kw)
        if r.status_code == 401:
            raise RuntimeError("a nuvem recusou o PAINEL_TOKEN — confira se é o mesmo do .env e da Vercel.")
        r.raise_for_status()
        return r.json()

    def estado(self) -> dict:
        return self._req("GET", "/api/pc/estado")

    def subir_candidaturas(self, entradas: list[dict]) -> None:
        self._req("PUT", "/api/pc/candidaturas", json=entradas)

    def subir_kit(self, id_: str, dados: str, gerado_em: str, pdf: Path | None) -> None:
        arquivos = {"pdf": (pdf.name, pdf.read_bytes(), "application/pdf")} if pdf else None
        self._req("PUT", f"/api/pc/kit/{id_}", data={"dados": dados, "gerado_em": gerado_em}, files=arquivos)

    def subir_vagas(self, relatorio: str, vagas: list[dict]) -> None:
        self._req("PUT", "/api/pc/vagas", json={"relatorio": relatorio, "vagas": vagas})

    def concluir(self, n: int, erro: str = "") -> None:
        self._req("POST", f"/api/pc/pedidos/{n}", json={"erro": erro})


def _horario(e: dict) -> str:
    return em_utc(e.get("atualizado_em", ""))


def sincronizar(config: dict, nuvem: Nuvem, mock: bool = False) -> dict:
    """Uma rodada. Devolve contagens do que fez (para o log)."""
    out_dir = Path(config.get("output_dir", "output"))
    kits_dir = out_dir / ("kits_mock" if mock else "kits")
    estado = Candidaturas(kits_dir / "candidaturas.json")
    resumo = {"baixadas": 0, "pedidos": 0, "erros": 0, "subidas": 0, "kits": 0, "vagas": 0}

    remoto = nuvem.estado()

    # 1) o que mudou na nuvem (status, anotações, "pular") vem para o PC
    locais = {e["link"]: e for e in estado.todas()}
    baixar = [e for e in remoto["candidaturas"]
              if e["link"] not in locais or e["atualizado_em"] > _horario(locais[e["link"]])]
    estado.substituir(baixar)
    resumo["baixadas"] = len(baixar)

    # 2) pedidos feitos pelo painel na nuvem
    vagas = []
    try:
        relatorio = kitmod.ultimo_relatorio(out_dir).name
        vagas = kitmod.carregar_vagas(out_dir)
    except SystemExit:
        relatorio = ""
    por_link = {v["link"]: v for v in vagas}
    for p in remoto["pedidos"]:
        e = next((x for x in estado.todas() if x["link"] == p["link"]), {})
        vaga = por_link.get(p["link"])
        if not vaga and e.get("descricao"):
            vaga = {**{k: e.get(k, "") for k in CAMPOS_VAGA}, "descricao": e["descricao"]}
        erro = ""
        if not vaga:
            erro = "a vaga não está no relatório de hoje e não tenho a descrição dela guardada"
        else:
            print(f"  pedido {p['tipo']}: {vaga['titulo']} — {vaga['empresa']}", flush=True)
            try:
                kitmod.gerar_sem_terminal(
                    config, vaga, kits_dir, mock=mock, refazer=p["tipo"] != "gerar",
                    faltando=None if p["tipo"] == "gerar" else (e.get("lacunas") or []))
            except Exception as ex:  # noqa: BLE001 — o erro vai pro painel e o próximo pedido segue
                traceback.print_exc()
                erro = str(ex)
        if erro:
            resumo["erros"] += 1
            if vaga:
                estado.atualizar(vaga, erro=erro)
        nuvem.concluir(p["n"], erro)
        resumo["pedidos"] += 1

    # 3) o que mudou no PC sobe: candidaturas, kits (com PDF) e as vagas do dia
    na_nuvem = {e["link"]: e["atualizado_em"] for e in remoto["candidaturas"]}
    subir = []
    for e in estado.todas():
        e = {**e, "atualizado_em": _horario(e)}
        if e["link"] not in na_nuvem or e["atualizado_em"] > na_nuvem[e["link"]]:
            subir.append(e)
    if subir:
        nuvem.subir_candidaturas(subir)
    resumo["subidas"] = len(subir)

    for e in estado.todas():
        kit_json = Path(e.get("pasta") or "") / "kit.json"
        if not e.get("pasta") or not kit_json.exists():
            continue
        dados = json.loads(kit_json.read_text(encoding="utf-8"))
        if remoto["kits"].get(e["id"]) == dados["gerado_em"]:
            continue
        pdf = Path(dados["pdf"]) if dados.get("pdf") and Path(dados["pdf"]).exists() else None
        nuvem.subir_kit(e["id"], json.dumps(dados, ensure_ascii=False), dados["gerado_em"], pdf)
        resumo["kits"] += 1

    if relatorio and relatorio != remoto["relatorio"]:
        lista = vagas_para_lista(vagas)
        nuvem.subir_vagas(relatorio, lista)
        resumo["vagas"] = len(lista)
    return resumo


@contextlib.contextmanager
def _uma_por_vez(path: Path):
    """Trava de arquivo: a rodada do rodar_diario.bat e a do --continuo nunca
    rodam juntas (os dois pegariam o mesmo pedido e gerariam o kit duas vezes).
    Rende False se a outra está no meio de uma rodada."""
    import msvcrt

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as f:
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--continuo", action="store_true", help=f"repete a cada {INTERVALO_S} s")
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args()

    url, token = _ler_env("PAINEL_URL"), _ler_env("PAINEL_TOKEN")
    if not url or not token:
        sys.exit("Faltam PAINEL_URL e PAINEL_TOKEN no .env (ver .env.example) — sem eles não há o que sincronizar.")
    config = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    nuvem = Nuvem(url, token)
    trava = Path(config.get("output_dir", "output")) / "sincronizar.lock"

    while True:
        agora = dt.datetime.now().strftime("%d/%m %H:%M")
        with _uma_por_vez(trava) as livre:
            if not livre:
                print(f"[{agora}] outra sincronização em andamento — pulando esta rodada.", flush=True)
            else:
                try:
                    r = sincronizar(config, nuvem)
                    if any(r.values()) or not args.continuo:
                        print(f"[{agora}] " + ", ".join(f"{k} {v}" for k, v in r.items()), flush=True)
                except (requests.RequestException, RuntimeError) as e:
                    # sem internet ou nuvem fora do ar: tenta de novo na próxima rodada
                    print(f"[{agora}] [aviso] {e}", file=sys.stderr, flush=True)
                    if not args.continuo:
                        sys.exit(1)
        if not args.continuo:
            return
        time.sleep(INTERVALO_S)


if __name__ == "__main__":
    main()

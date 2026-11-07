#!/usr/bin/env python3
"""
Liga o PC ao painel de candidaturas na nuvem (pasta web/, publicada na Vercel).

Chamado pelo rodar_diario.bat depois da coleta:
1. sobe as vagas "aplicar agora" e "avaliar" do relatório do dia, para a aba
   "Vagas de hoje" (só quando as vagas mudaram, mesmo que o nome do relatório
   seja o mesmo) — as do Perfil 1 (vagas_triadas_*) e as do último relatório
   do Perfil 2 (vagas_intl_*), cada uma marcada com o `perfil`;
2. sobe o mapa de lacunas do mesmo relatório (lacunas.py), para a aba
   "Lacunas";
3. baixa as candidaturas registradas no painel para output/candidaturas.json —
   uma cópia de segurança no PC, caso o banco da nuvem se perca — e, do mesmo
   jeito, as vagas descartadas (output/descartes.json, com o motivo) e a sua
   avaliação das lacunas (output/lacunas_revisao.json).

Uso:
    python sincronizar.py

Precisa de PAINEL_URL (ex.: https://radar-de-vagas.vercel.app) e PAINEL_TOKEN
no .env — o token é o mesmo cadastrado na Vercel.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path

import requests
import yaml

from novidades import _ler_env

# o que a aba "Vagas de hoje" recebe de cada vaga do relatório
CAMPOS_LISTA = ["nota_fit", "recomendacao", "titulo", "empresa", "fonte", "cidade", "remoto",
                "salario", "publicado_em", "idade_dias", "adicionada_em", "alerta", "trilha", "link",
                "perfil", "elegibilidade", "tipo_empresa", "ingles_exigido"]
# (padrão do relatório, perfil) — o Perfil 2 vem depois na ordem do painel
RELATORIOS = [("vagas_triadas_*.csv", "br"), ("vagas_intl_*.csv", "intl")]


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

    def subir_vagas(self, relatorio: str, vagas: list[dict], assinatura: str, lacunas: dict | None = None) -> None:
        self._req("PUT", "/api/pc/vagas", json={"relatorio": relatorio, "vagas": vagas, "assinatura": assinatura,
                                                "lacunas": lacunas or {}})


def assinatura(vagas) -> str:
    """Impressão digital do conteúdo das vagas. Comparar só o nome do relatório
    não basta: uma 2ª rodada no mesmo dia regrava vagas_triadas_<data>.csv com
    o mesmo nome (28/10/2026: a rodada da madrugada e a das 11h, e o painel
    ficou com as vagas da madrugada)."""
    return hashlib.sha1(json.dumps(vagas, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:12]


def vagas_do_relatorio(out_dir: Path) -> tuple[str, list[dict]]:
    """(nome do último relatório, vagas "aplicar agora" e "avaliar" dele).
    Junta o último relatório de cada perfil; o nome devolvido é o do Perfil 1
    (o painel tira a data dele), ou o do Perfil 2 se só ele existir."""
    nome, vagas = "", []
    for ordem, (padrao, perfil) in enumerate(RELATORIOS):
        relatorios = sorted(out_dir.glob(padrao))
        if not relatorios:
            continue
        nome = nome or relatorios[-1].name
        with relatorios[-1].open(encoding="utf-8-sig") as f:
            linhas = list(csv.DictReader(f))
        for n, v in enumerate(linhas, 1):
            if v.get("recomendacao") not in ("aplicar_agora", "avaliar"):
                continue
            vagas.append({**{k: v.get(k, "") for k in CAMPOS_LISTA}, "perfil": perfil, "relatorio": relatorios[-1].name,
                          "n": ordem * 100_000 + n, "id": hashlib.md5(v["link"].encode()).hexdigest()[:12]})
    return nome, vagas


def lacunas_do_relatorio(config: dict) -> dict:
    """Mapa de lacunas (lacunas.py) para a aba "Lacunas" do painel. Sem cv.txt
    ou sem a coleta, segue sem ele — as vagas do dia sobem do mesmo jeito."""
    try:
        import lacunas

        return lacunas.calcular(config)
    except (Exception, SystemExit) as e:  # noqa: BLE001 — o mapa é extra; a sincronização não pode cair por ele
        print(f"  [aviso] mapa de lacunas não calculado ({e}); o painel fica com o anterior.", file=sys.stderr)
        return {}


def sincronizar(config: dict, nuvem: Nuvem) -> dict:
    """Uma rodada. Devolve contagens do que fez (para o log)."""
    out_dir = Path(config.get("output_dir", "output"))
    remoto = nuvem.estado()

    relatorio, vagas = vagas_do_relatorio(out_dir)
    subidas = 0
    if relatorio:
        lacunas = lacunas_do_relatorio(config)
        # a assinatura cobre as lacunas também: sem isso, a 1ª rodada depois
        # desta mudança não subiria o mapa (as vagas eram as mesmas)
        marca = assinatura({"vagas": vagas, "lacunas": lacunas})
        if marca != remoto.get("assinatura"):
            nuvem.subir_vagas(relatorio, vagas, marca, lacunas)
            subidas = len(vagas)

    copia = out_dir / "candidaturas.json"
    copia.parent.mkdir(parents=True, exist_ok=True)
    copia.write_text(json.dumps(remoto["candidaturas"], ensure_ascii=False, indent=1), encoding="utf-8")
    for nome in ("descartes", "lacunas_revisao"):
        if nome in remoto:  # painel antigo, sem essas rotas: não apaga a cópia anterior
            (out_dir / f"{nome}.json").write_text(json.dumps(remoto[nome], ensure_ascii=False, indent=1),
                                                  encoding="utf-8")
    return {"vagas": subidas, "candidaturas": len(remoto["candidaturas"])}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args()

    url, token = _ler_env("PAINEL_URL"), _ler_env("PAINEL_TOKEN")
    if not url or not token:
        sys.exit("Faltam PAINEL_URL e PAINEL_TOKEN no .env (ver .env.example) — sem eles não há o que sincronizar.")
    config = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    try:
        r = sincronizar(config, Nuvem(url, token))
    except (requests.RequestException, RuntimeError) as e:
        sys.exit(f"[aviso] não consegui falar com o painel: {e}")
    print(f"Painel: {r['vagas']} vagas do dia enviadas, {r['candidaturas']} candidaturas copiadas "
          f"para output/candidaturas.json.")


if __name__ == "__main__":
    main()

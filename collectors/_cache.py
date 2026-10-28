"""Cache, entre rodadas, do que custa uma requisição por vaga: a descrição
(InHire, Shopee, SmartRecruiters, Workday, beBee) e a página da vaga do
Remote.io. Sem ele, cada rodada rebaixava ~900 páginas de vagas já vistas
(relatório de 28/10/2026) e o beBee ficava com o resumo de 300 caracteres no
vagas_coletadas.json sempre que a nota era reaproveitada.

O screen.py chama carregar() antes da coleta e salvar() depois. Só as entradas
usadas na rodada são salvas, então vaga encerrada sai sozinha. Sem carregar()
(testes), o cache começa vazio e fica só na memória.
"""
from __future__ import annotations

import datetime as dt
import json
import threading
from pathlib import Path

# depois disso a página é baixada de novo, pra pegar edição do anúncio
TTL_DIAS = 7

_DADOS: dict[str, dict] = {}  # chave -> {"v": valor, "em": "AAAA-MM-DD"}
_USADAS: set[str] = set()
_LOCK = threading.Lock()


def carregar(path: Path) -> None:
    with _LOCK:
        _DADOS.clear()
        _USADAS.clear()
        try:
            _DADOS.update(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass  # sem arquivo (1ª rodada) ou ilegível: começa do zero


def obter(chave: str, ttl_dias: int = TTL_DIAS):
    """O valor guardado, ou None se não há ou venceu."""
    with _LOCK:
        e = _DADOS.get(chave)
        if not e:
            return None
        try:
            idade = (dt.date.today() - dt.date.fromisoformat(e["em"])).days
        except (KeyError, ValueError):
            return None
        if idade > ttl_dias:
            return None
        _USADAS.add(chave)
        return e["v"]


def guardar(chave: str, valor) -> None:
    with _LOCK:
        _DADOS[chave] = {"v": valor, "em": dt.date.today().isoformat()}
        _USADAS.add(chave)


def salvar(path: Path) -> None:
    with _LOCK:
        usadas = {k: _DADOS[k] for k in _USADAS if k in _DADOS}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(usadas, ensure_ascii=False), encoding="utf-8")


def descricao(chave: str, buscar) -> str:
    """A descrição do cache ou, se não houver, `buscar()` — guardada quando
    não vem vazia (vazia costuma ser falha de rede; tenta de novo na próxima)."""
    texto = obter(chave)
    if texto is None:
        texto = buscar()
        if texto:
            guardar(chave, texto)
    return texto or ""

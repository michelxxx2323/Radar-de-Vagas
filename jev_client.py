"""
Cliente para a API do Jev (TypeSafe AI) — https://docs.typesafe.ai/api

Uso real (com chave):
    client = JevClient(api_key="...")
    resp = client.evaluate(state, questions)

Uso offline / sem chave (para testar o resto do pipeline):
    client = MockJevClient()
    resp = client.evaluate(state, questions)

Ambos devolvem o mesmo formato de resposta da API real:
    {"model": "...", "answers": {"<id>": {...}}, "usage": {...}}
"""
from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass

import requests

from novidades import _ler_env

JEV_ENDPOINT = "https://api.typesafe.ai/v1/systemone"


class JevError(RuntimeError):
    pass


@dataclass
class JevClient:
    api_key: str | None = None
    model: str = "jev-latest"
    max_retries: int = 5
    timeout: int = 30

    def __post_init__(self):
        # variável de ambiente ou, se não houver, a linha do .env desta pasta
        # (a tarefa agendada roda sem o shell do usuário, então o .env basta)
        self.api_key = self.api_key or _ler_env("TYPESAFE_API_KEY")
        if not self.api_key:
            raise JevError(
                "Nenhuma TYPESAFE_API_KEY encontrada. Coloque a chave no .env desta pasta "
                "(veja .env.example) ou rode com --mock para testar sem chave."
            )

    def evaluate(self, state, questions: dict) -> dict:
        """Chama POST /v1/systemone. Faz retry com backoff exponencial em 429/529,
        como recomendado pela documentação."""
        body = {"state": state, "model": self.model, "questions": questions}
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        delay = 1.0
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = requests.post(
                    JEV_ENDPOINT, json=body, headers=headers, timeout=self.timeout
                )
            except (requests.Timeout, requests.ConnectionError) as e:
                # instabilidade de rede/servidor: tenta de novo em vez de derrubar a rodada
                if attempt == self.max_retries:
                    raise JevError(f"Jev sem resposta após {attempt} tentativas: {e}") from e
                time.sleep(delay + random.uniform(0, 0.5))
                delay *= 2
                continue

            if resp.status_code == 200:
                return resp.json()

            # 429/529 = sobrecarga (retry recomendado pela doc); 5xx/52x = erro
            # transitório do servidor ou do Cloudflare na frente dele.
            if resp.status_code in (429, 529) or resp.status_code >= 500:
                if attempt == self.max_retries:
                    raise JevError(
                        f"Jev respondeu {resp.status_code} após {attempt} tentativas: {resp.text[:300]}"
                    )
                time.sleep(delay + random.uniform(0, 0.5))
                delay *= 2
                continue

            if resp.status_code == 401:
                raise JevError("401 Unauthorized — confira sua TYPESAFE_API_KEY.")

            if resp.status_code == 422:
                raise JevError(f"422 Unprocessable Entity — requisição inválida: {resp.text[:500]}")

            raise JevError(f"Erro inesperado ({resp.status_code}): {resp.text[:500]}")

        raise JevError("Esgotadas as tentativas de chamada à API do Jev.")


# ---------------------------------------------------------------------------
# Cliente simulado (sem rede, sem chave) — mesma interface do JevClient real.
# Usa sobreposição simples de palavras-chave entre o CV e a vaga para gerar
# respostas plausíveis no mesmo formato da API, só para validar o pipeline
# (coleta -> pontuação -> ranking -> CSV) de ponta a ponta sem depender de
# rede externa ou de uma chave de API.
# ---------------------------------------------------------------------------

_STOPWORDS = set(
    "de da do das dos e a o as os em um uma para com por que na no nas nos "
    "ao aos sua seu suas seus como mais menos ser ter ou se sem sob sobre "
    "the a an of and or to in on for with is are be as at from by".split()
)


def _tokenize(text: str) -> set[str]:
    words = re.findall(r"[a-záàâãéêíóôõúüç0-9]+", (text or "").lower())
    return {w for w in words if len(w) > 2 and w not in _STOPWORDS}


class MockJevClient:
    """Não chama nenhuma rede. Gera respostas heurísticas (sobreposição de
    palavras entre currículo e vaga) apenas para testar o restante do
    pipeline sem precisar de uma TYPESAFE_API_KEY real."""

    def __init__(self, model: str = "jev-mock", **_ignored):
        self.model = model

    def evaluate(self, state, questions: dict) -> dict:
        cv_tokens = _tokenize(state.get("cv", "")) if isinstance(state, dict) else set()
        vaga = state.get("vaga", {}) if isinstance(state, dict) else {}
        vaga_text = " ".join(str(v) for v in vaga.values())
        vaga_tokens = _tokenize(vaga_text)

        overlap = len(cv_tokens & vaga_tokens)
        # Cobertura: fração do vocabulário da vaga que aparece no CV. O CV tende a ser um
        # documento grande e fixo, então um Jaccard contra a união fica achatado demais para
        # diferenciar vagas boas de ruins — a cobertura do lado da vaga é um sinal melhor aqui.
        jaccard = overlap / max(len(vaga_tokens), 1)

        answers = {}
        for qid, q in questions.items():
            qtype = q["type"]
            if qtype == "score":
                n_levels = len(q["criteria"])
                raw = min(jaccard * n_levels, n_levels - 1)
                level = max(0, min(n_levels - 1, round(raw)))
                probs = {str(i): 0.0 for i in range(n_levels)}
                probs[str(level)] = 1.0
                answers[qid] = {
                    "type": "score",
                    "score": float(level),
                    "legend": {str(i): c if isinstance(c, str) else str(c) for i, c in enumerate(q["criteria"])},
                    "probabilities": probs,
                    "confidence": round(0.5 + jaccard, 2),
                }
            elif qtype == "noul":
                val = 1.0 if jaccard > 0.15 else 0.0
                answers[qid] = {"type": "noul", "noul": val}
            elif qtype == "choice":
                criteria = q["criteria"]
                options = list(criteria.keys())

                def _option_score(opt: str) -> int:
                    desc = criteria.get(opt)
                    desc_tokens = _tokenize(desc) if isinstance(desc, str) else set()
                    return len((_tokenize(opt) | desc_tokens) & vaga_tokens)

                scores = {o: _option_score(o) for o in options}
                if options and max(scores.values(), default=0) > 0:
                    best = max(options, key=lambda o: scores[o])
                else:
                    # sem sinal lexical nas descrições das opções: assume que o dict de
                    # critérios foi escrito da melhor pra pior opção (convenção deste
                    # projeto) e usa a cobertura léxica geral currículo/vaga pra escolher
                    # um ponto nessa escala.
                    idx = round((1 - jaccard) * (len(options) - 1)) if options else 0
                    best = options[idx] if options else None
                probs = {o: (1.0 if o == best else 0.0) for o in options}
                answers[qid] = {
                    "type": "choice",
                    "choice": best,
                    "probabilities": probs,
                    "confidence": round(0.4 + jaccard, 2),
                }
        return {
            "model": self.model,
            "answers": answers,
            "usage": {"input_tokens": 0, "output_tokens": 0},
        }

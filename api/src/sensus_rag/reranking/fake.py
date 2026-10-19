"""Reranker determinístico, para teste.

Pontua por sobreposição de palavras entre pergunta e trecho. Não é um cross-encoder e
não pretende ser: existe para que o decorador e o harness sejam exercitados sem gastar
cota, com um duplo cuja ordem é previsível. O pipeline em memória NÃO o usa — sem chave
da Voyage o `build_rag` monta o retriever sem rerank nenhum, e um duplo ali faria a
montagem em memória prometer um estágio que a real só tem com provedor.

Empate resolve pela posição do RRF — um duplo que devolvesse ordens diferentes entre
execuções tornaria sem sentido qualquer medição feita em cima dele.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace

from sensus_rag.contract import RetrievedChunk

_WORD = re.compile(r"\w+")


def _words(text: str) -> set[str]:
    return {match.group().casefold() for match in _WORD.finditer(text)}


@dataclass(frozen=True)
class FakeReranker:
    async def rerank(
        self, *, question: str, chunks: Sequence[RetrievedChunk], top_k: int
    ) -> list[RetrievedChunk]:
        asked = _words(question)
        divisor = len(asked) or 1
        scored = [
            (len(asked & _words(chunk.content)) / divisor, position, chunk)
            for position, chunk in enumerate(chunks, start=1)
        ]
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [
            replace(chunk, rerank_score=score, fused_rank=position)
            for score, position, chunk in scored[:top_k]
        ]

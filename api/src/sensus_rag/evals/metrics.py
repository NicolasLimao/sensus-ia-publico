"""recall@k e MRR.

O acerto é "o chunk devolvido contém o trecho anotado, no documento certo" — não
igualdade de id. Comparação normalizada por caixa e espaço, porque o chunker pode
recompor espaçamento sem que isso signifique erro de recuperação. Nota: normalização
usa NFKC (forma de composição), não remove acento — "até" e "ate" continuam diferentes.
"""

import re
import unicodedata
from collections.abc import Sequence

from sensus_rag.contract import RetrievedChunk
from sensus_rag.evals.dataset import DatasetEntry

_WHITESPACE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    """Normalizar forma de composição, espaços e caixa (não remove acentos)."""
    composed = unicodedata.normalize("NFKC", text)
    return _WHITESPACE.sub(" ", composed).strip().casefold()


def hit_at(entry: DatasetEntry, results: Sequence[RetrievedChunk]) -> int | None:
    """Posição 1-indexada do primeiro acerto, ou None se não houve."""
    target = _normalize(entry.trecho_esperado)
    for position, chunk in enumerate(results, start=1):
        if chunk.document_name == entry.documento and target in _normalize(chunk.content):
            return position
    return None


def recall_at_k(positions: Sequence[int | None], *, k: int) -> float:
    if not positions:
        return 0.0
    hits = sum(1 for p in positions if p is not None and p <= k)
    return hits / len(positions)


def mean_reciprocal_rank(positions: Sequence[int | None]) -> float:
    if not positions:
        return 0.0
    return sum(0.0 if p is None else 1.0 / p for p in positions) / len(positions)


def rate(flags: Sequence[bool]) -> float:
    """Proporção de verdadeiros.

    Serve às duas métricas de geração, que só têm sentido em par:

    - `taxa_de_recusa` sobre as perguntas SEM resposta — mede falso positivo, o
      sistema inventando.
    - `taxa_de_resposta` sobre as perguntas COM resposta — mede falso negativo, o
      sistema recusando o que sabe.

    Sem a segunda, a primeira se engana sozinha: um sistema que recusa tudo teria
    falso positivo perfeito.
    """
    if not flags:
        return 0.0
    return sum(1 for flag in flags if flag) / len(flags)

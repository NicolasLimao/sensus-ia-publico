"""Reciprocal Rank Fusion.

Cada chunk soma 1/(k + posição) em cada lista onde aparece. Só a posição entra na
conta, então as duas pernas da busca podem ter escalas completamente diferentes sem
precisar de normalização nem de peso calibrado no escuro.

As pernas chegam nomeadas, e não como uma lista genérica de rankings, porque a posição
em cada uma delas sai no resultado (SPEC §3.2): para dizer "este chunk foi o 1º na
semântica e não apareceu na lexical" é preciso saber qual lista é qual.
"""

from collections.abc import Sequence

from sensus_rag.contract import RetrievedChunk, ScoredChunk


def reciprocal_rank_fusion(
    *,
    semantic: Sequence[ScoredChunk],
    lexical: Sequence[ScoredChunk],
    k: int,
    limit: int,
) -> list[RetrievedChunk]:
    sums: dict[str, float] = {}
    seen: dict[str, ScoredChunk] = {}
    semantic_rank: dict[str, int] = {}
    lexical_rank: dict[str, int] = {}

    for ranking, positions in ((semantic, semantic_rank), (lexical, lexical_rank)):
        for position, item in enumerate(ranking, start=1):
            sums[item.chunk_id] = sums.get(item.chunk_id, 0.0) + 1.0 / (k + position)
            seen.setdefault(item.chunk_id, item)
            # `setdefault`: se a mesma perna devolvesse o chunk duas vezes, a melhor
            # posição é a que vale.
            positions.setdefault(item.chunk_id, position)

    ordered = sorted(sums.items(), key=lambda pair: (-pair[1], pair[0]))
    return [
        RetrievedChunk(
            chunk_id=chunk_id,
            document_id=seen[chunk_id].document_id,
            document_name=seen[chunk_id].document_name,
            content=seen[chunk_id].content,
            score=total,
            semantic_rank=semantic_rank.get(chunk_id),
            lexical_rank=lexical_rank.get(chunk_id),
        )
        for chunk_id, total in ordered[:limit]
    ]

"""Busca híbrida: duas consultas independentes, fundidas por RRF.

As pernas são pedidas em paralelo — são consultas distintas ao mesmo banco e não
dependem uma da outra.
"""

import asyncio
import logging
from dataclasses import dataclass

from sensus_rag.contract import ChunkRepository, Embedder, Reranker, RetrievedChunk, Retriever
from sensus_rag.errors import RerankUnavailable
from sensus_rag.fusion import reciprocal_rank_fusion

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HybridRetriever:
    repository: ChunkRepository
    embedder: Embedder
    candidates: int = 40
    rrf_k: int = 60

    async def search(self, *, tenant_id: str, question: str, top_k: int) -> list[RetrievedChunk]:
        vector = await self.embedder.embed_query(question)
        semantic, lexical = await asyncio.gather(
            self.repository.search_semantic(
                tenant_id=tenant_id, embedding=vector, limit=self.candidates
            ),
            self.repository.search_lexical(
                tenant_id=tenant_id, query=question, limit=self.candidates
            ),
        )
        return reciprocal_rank_fusion(semantic=semantic, lexical=lexical, k=self.rrf_k, limit=top_k)


@dataclass(frozen=True)
class RerankingRetriever:
    """Decora um `Retriever` com um cross-encoder. O `HybridRetriever` não muda.

    Pede `candidates` ao interno e devolve `top_k`: o rerank só tem o que fazer se
    receber mais candidatos do que vai entregar.

    `top_k` é promessa do decorador, não do reranker: um reranker que devolva menos
    do que foi pedido — o `VoyageReranker` faz isso quando o corte de orçamento tira
    a cauda da lista — tem o resto completado na ordem fundida, com `rerank_score`
    nulo, que é como a rota ensina a ler "não passou por rerank".

    Falha do rerank cai para a ordem fundida e loga — o usuário recebe resposta um
    pouco pior em vez de erro. O harness NÃO usa este decorador, de propósito: lá uma
    queda silenciosa produziria um A/B que parece válido e não é.
    """

    inner: Retriever
    reranker: Reranker
    candidates: int = 20

    async def search(self, *, tenant_id: str, question: str, top_k: int) -> list[RetrievedChunk]:
        found = await self.inner.search(
            tenant_id=tenant_id, question=question, top_k=max(self.candidates, top_k)
        )
        try:
            ordered = await self.reranker.rerank(question=question, chunks=found, top_k=top_k)
        except RerankUnavailable:
            logger.warning(
                "rerank indisponível: seguindo com a ordem fundida do RRF", exc_info=True
            )
            return found[:top_k]

        if len(ordered) >= top_k:
            return ordered[:top_k]

        # Faltou candidato reordenado (corte de orçamento do provedor). Encolher a
        # resposta em silêncio faria `/search` devolver menos do que pediram sem dizer
        # por quê; a cauda do RRF completa a lista.
        reranked_ids = {chunk.chunk_id for chunk in ordered}
        tail = [chunk for chunk in found if chunk.chunk_id not in reranked_ids]
        return ordered + tail[: top_k - len(ordered)]

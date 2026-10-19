"""Montagem do pipeline de RAG.

Com banco e chave configurados, sobe o pipeline real. Sem eles, sobe em memória — a
aplicação continua subindo para quem só quer mexer no front, e o teste de rota nunca
toca rede. É o mesmo princípio do `DemoUpstream` da fatia 1.

`build_rag` nunca abre o pool nem faz uma chamada de rede: `AsyncConnectionPool(...,
open=False)` só conecta quando `.open()` é chamado. Quem abre e fecha os recursos é o
`lifespan` de `sensus_api.app`, com o ciclo de vida da aplicação — este módulo só monta.
"""

import logging
from dataclasses import dataclass, replace

import httpx
from psycopg_pool import AsyncConnectionPool

from sensus_rag.chunking import StructuralChunker
from sensus_rag.config import RagSettings, connection_url
from sensus_rag.contract import AnswerGenerator, ChunkRepository, Embedder, Reranker, Retriever
from sensus_rag.embedding.fake import FakeEmbedder
from sensus_rag.embedding.voyage import VoyageEmbedder
from sensus_rag.generation.openai import OpenAIAnswerGenerator
from sensus_rag.ingestion import DocumentIngestor
from sensus_rag.rate_limit import limiter_from
from sensus_rag.repository.memory import InMemoryChunkRepository
from sensus_rag.repository.postgres import PostgresChunkRepository
from sensus_rag.reranking.voyage import VoyageReranker
from sensus_rag.retrieval import HybridRetriever, RerankingRetriever
from sensus_rag.tenant_scope import TenantScope

logger = logging.getLogger(__name__)


def uses_real_pipeline(settings: RagSettings) -> bool:
    return bool(settings.database_url) and bool(settings.voyage_api_key)


def uses_generation(settings: RagSettings) -> bool:
    """A geração é o que decide qual upstream sobe — não o banco.

    Deliberado: quem configurou uma chave de verdade não pode receber resposta de
    demonstração sem perceber. Sem banco, o pipeline de RAG é o em memória, e o fluxo
    "subo, faço upload, converso" continua funcionando no mesmo processo.
    """
    return bool(settings.openai_api_key) and bool(settings.generation_model)


def uses_rerank(settings: RagSettings) -> bool:
    """O modelo é o interruptor — a chave já é a do embedding."""
    return bool(settings.rerank_model)


@dataclass(frozen=True)
class RagResources:
    """Recursos com ciclo de vida próprio, abertos e fechados junto da aplicação.

    Vazio (todos os campos `None`) no pipeline em memória — não há nada para abrir
    nem fechar, e `open`/`close` viram no-op de propósito: o `lifespan` chama os dois
    sempre, sem precisar saber qual pipeline subiu.
    """

    pool: AsyncConnectionPool | None = None
    http_client: httpx.AsyncClient | None = None
    generation_client: httpx.AsyncClient | None = None

    async def open(self) -> None:
        if self.pool is not None:
            await self.pool.open()

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()
        if self.http_client is not None:
            await self.http_client.aclose()
        if self.generation_client is not None:
            await self.generation_client.aclose()


@dataclass(frozen=True)
class RagPipeline:
    """As três peças do pipeline, mais os recursos que precisam de `open`/`close`."""

    ingestor: DocumentIngestor
    retriever: Retriever
    repository: ChunkRepository
    resources: RagResources
    generator: AnswerGenerator | None = None


def build_rag(settings: RagSettings) -> RagPipeline:
    repository: ChunkRepository
    embedder: Embedder
    resources = RagResources()
    reranker: Reranker | None = None

    if uses_real_pipeline(settings):
        pool = AsyncConnectionPool(connection_url(settings), open=False)
        repository = PostgresChunkRepository(
            scope=TenantScope(pool=pool), dimensions=settings.embedding_dimensions
        )
        http_client = httpx.AsyncClient(timeout=60.0)
        # Embedder e reranker batem na MESMA conta Voyage: uma única instância de
        # limitador para os dois, senão cada um acredita ter a cota inteira.
        voyage_limiter = limiter_from(
            settings.embedding_requests_per_minute,
            settings.embedding_tokens_per_minute,
        )
        embedder = VoyageEmbedder(
            api_key=settings.voyage_api_key,
            model=settings.embedding_model,
            dimensions=settings.embedding_dimensions,
            client=http_client,
            limiter=voyage_limiter,
            max_batch_tokens=settings.embedding_max_batch_tokens,
        )
        if uses_rerank(settings):
            reranker = VoyageReranker(
                api_key=settings.voyage_api_key,
                model=settings.rerank_model,
                client=http_client,
                limiter=voyage_limiter,
                max_request_tokens=settings.rerank_max_request_tokens,
                max_document_chars=settings.rerank_max_document_chars,
            )
        resources = RagResources(pool=pool, http_client=http_client)
    else:
        missing = [
            name
            for name, value in (
                ("SENSUS_DATABASE_URL", settings.database_url),
                ("SENSUS_VOYAGE_API_KEY", settings.voyage_api_key),
            )
            if not value
        ]
        logger.warning(
            "pipeline em memória: sem %s, a base não persiste e os embeddings são falsos",
            " e/ou ".join(missing),
        )
        repository = InMemoryChunkRepository()
        embedder = FakeEmbedder(dimensions=settings.embedding_dimensions)

    ingestor = DocumentIngestor(
        chunker=StructuralChunker(
            target_chars=settings.chunk_target_chars,
            overlap_chars=settings.chunk_overlap_chars,
        ),
        embedder=embedder,
        repository=repository,
        max_bytes=settings.max_upload_bytes,
    )
    retriever: Retriever = HybridRetriever(
        repository=repository,
        embedder=embedder,
        candidates=settings.retrieval_candidates,
        rrf_k=settings.rrf_k,
    )
    if reranker is not None:
        retriever = RerankingRetriever(
            inner=retriever,
            reranker=reranker,
            candidates=settings.rerank_candidates,
        )

    generator: AnswerGenerator | None = None
    if uses_generation(settings):
        generation_client = httpx.AsyncClient(timeout=settings.generation_timeout_seconds)
        # Sem `model` nem `max_context_chars`: desde a fatia 4a eles vêm no `config`
        # de cada requisição, resolvido a partir da configuração do tenant. O que fica
        # aqui é o que é de processo — chave, cliente HTTP, prazo e limitador.
        generator = OpenAIAnswerGenerator(
            api_key=settings.openai_api_key,
            client=generation_client,
            timeout_seconds=settings.generation_timeout_seconds,
            limiter=limiter_from(settings.generation_requests_per_minute, 0),
        )
        resources = replace(resources, generation_client=generation_client)
    # Sem geração configurada, `generator` fica `None` e nenhum aviso sai daqui: quem
    # decide se isso vira modo demonstração é `sensus_api.app`, que sabe se um
    # `upstream` foi injetado — `build_rag` não sabe e não deveria avisar por conta
    # própria (inversão de camada).

    return RagPipeline(
        ingestor=ingestor,
        retriever=retriever,
        repository=repository,
        resources=resources,
        generator=generator,
    )

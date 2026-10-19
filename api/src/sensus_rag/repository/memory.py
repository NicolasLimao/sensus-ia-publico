"""Repositório em memória — duplo de teste.

Cosseno na perna semântica e casamento de termos na lexical. Não pretende igualar o
Postgres: o comportamento real é verificado nos testes de integração. O que ele
garante é que quem consome o repositório está correto.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sensus_rag.contract import DocumentSummary, EmbeddedChunk, ScoredChunk, Vector


@dataclass
class _Document:
    id: str
    tenant_id: str
    name: str
    content_hash: str
    created_at: datetime
    chunks: list[EmbeddedChunk]


@dataclass
class InMemoryChunkRepository:
    _documents: dict[str, _Document] = field(default_factory=dict)

    async def upsert_document(
        self,
        *,
        tenant_id: str,
        name: str,
        content_type: str,
        content_hash: str,
        raw_text: str,
        chunks: Sequence[EmbeddedChunk],
    ) -> str:
        # content_type e raw_text são exigidos pelo Protocol, mas o duplo em memória
        # não precisa guardá-los — só content_hash importa para a idempotência.
        existing = next(
            (
                d
                for d in self._documents.values()
                if d.tenant_id == tenant_id and d.content_hash == content_hash
            ),
            None,
        )
        document_id = existing.id if existing else str(uuid.uuid4())
        self._documents[document_id] = _Document(
            id=document_id,
            tenant_id=tenant_id,
            name=name,
            content_hash=content_hash,
            created_at=existing.created_at if existing else datetime.now(UTC),
            chunks=list(chunks),
        )
        return document_id

    async def list_documents(self, *, tenant_id: str) -> list[DocumentSummary]:
        return [
            DocumentSummary(
                id=d.id, name=d.name, chunk_count=len(d.chunks), created_at=d.created_at
            )
            for d in self._documents.values()
            if d.tenant_id == tenant_id
        ]

    async def delete_document(self, *, tenant_id: str, document_id: str) -> bool:
        document = self._documents.get(document_id)
        if document is None or document.tenant_id != tenant_id:
            return False
        del self._documents[document_id]
        return True

    async def search_semantic(
        self, *, tenant_id: str, embedding: Vector, limit: int
    ) -> list[ScoredChunk]:
        scored = [
            (self._cosine(embedding, item.embedding), document, item)
            for document, item in self._all(tenant_id)
        ]
        scored.sort(key=lambda t: -t[0])
        return [self._score(d, i, score) for score, d, i in scored[:limit]]

    async def search_lexical(self, *, tenant_id: str, query: str, limit: int) -> list[ScoredChunk]:
        terms = {t for t in query.lower().split() if len(t) > 2}
        scored = []
        for document, item in self._all(tenant_id):
            words = set(item.chunk.content.lower().split())
            hits = len(terms & words)
            if hits:
                scored.append((float(hits), document, item))
        scored.sort(key=lambda t: -t[0])
        return [self._score(d, i, score) for score, d, i in scored[:limit]]

    def _all(self, tenant_id: str) -> list[tuple[_Document, EmbeddedChunk]]:
        return [
            (d, item)
            for d in self._documents.values()
            if d.tenant_id == tenant_id
            for item in d.chunks
        ]

    def _score(self, document: _Document, item: EmbeddedChunk, score: float) -> ScoredChunk:
        return ScoredChunk(
            chunk_id=f"{document.id}:{item.chunk.index}",
            document_id=document.id,
            document_name=document.name,
            content=item.chunk.content,
            score=score,
        )

    def _cosine(self, a: Vector, b: Vector) -> float:
        # Os vetores do FakeEmbedder já são unitários; o produto interno basta.
        return sum(x * y for x, y in zip(a, b, strict=True))

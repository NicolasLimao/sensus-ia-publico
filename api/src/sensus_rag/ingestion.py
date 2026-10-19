"""Do arquivo enviado aos chunks indexados.

A ordem importa: o teto de tamanho é verificado antes de qualquer trabalho, e o hash
sai dos bytes crus antes do embedding — a etapa cara — para que a chave de idempotência
não dependa de nada que a extração, o chunking ou o provedor produzam. Trocar o modelo
de embedding não pode mudar a identidade de um documento já indexado.
"""

import hashlib
from dataclasses import dataclass

from sensus_rag.contract import Chunker, ChunkRepository, EmbeddedChunk, Embedder, IngestionResult
from sensus_rag.errors import DocumentTooLarge, EmptyDocument
from sensus_rag.extraction.detect import extractor_for


@dataclass(frozen=True)
class DocumentIngestor:
    chunker: Chunker
    embedder: Embedder
    repository: ChunkRepository
    max_bytes: int

    async def ingest(
        self, *, tenant_id: str, name: str, content_type: str, data: bytes
    ) -> IngestionResult:
        if len(data) > self.max_bytes:
            raise DocumentTooLarge(f"'{name}' passa do limite de {self.max_bytes} bytes.")

        content_hash = hashlib.sha256(data).hexdigest()

        extractor = extractor_for(content_type, name)
        text = extractor.extract(data, name)
        if not text.strip():
            raise EmptyDocument(f"Nenhum texto foi extraído de '{name}'.")

        chunks = self.chunker.split(text)
        if not chunks:
            raise EmptyDocument(f"Nenhum trecho foi produzido a partir de '{name}'.")

        vectors = await self.embedder.embed_documents([c.content for c in chunks])
        embedded = [
            EmbeddedChunk(chunk=chunk, embedding=vector)
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]

        document_id = await self.repository.upsert_document(
            tenant_id=tenant_id,
            name=name,
            content_type=content_type,
            content_hash=content_hash,
            raw_text=text,
            chunks=embedded,
        )
        return IngestionResult(document_id=document_id, name=name, chunk_count=len(embedded))

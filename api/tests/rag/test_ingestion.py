"""Orquestração do upload, com fakes em todas as etapas — nenhuma rede, nenhum banco."""

import pytest

from sensus_rag.chunking import StructuralChunker
from sensus_rag.embedding.fake import FakeEmbedder
from sensus_rag.errors import DocumentTooLarge, EmptyDocument, UnsupportedDocument
from sensus_rag.ingestion import DocumentIngestor
from sensus_rag.repository.memory import InMemoryChunkRepository


def ingestor(repo: InMemoryChunkRepository, max_bytes: int = 1_000_000) -> DocumentIngestor:
    return DocumentIngestor(
        chunker=StructuralChunker(target_chars=40, overlap_chars=0),
        embedder=FakeEmbedder(dimensions=16),
        repository=repo,
        max_bytes=max_bytes,
    )


async def test_indexa_texto_puro_e_devolve_a_contagem() -> None:
    repo = InMemoryChunkRepository()
    text = "\n\n".join(["A" * 30, "B" * 30, "C" * 30])

    result = await ingestor(repo).ingest(
        tenant_id="t1", name="nota.txt", content_type="text/plain", data=text.encode()
    )

    assert result.name == "nota.txt"
    assert result.chunk_count == 3
    assert len(await repo.list_documents(tenant_id="t1")) == 1


async def test_reenviar_o_mesmo_arquivo_nao_duplica() -> None:
    repo = InMemoryChunkRepository()
    data = b"conteudo estavel"

    first = await ingestor(repo).ingest(
        tenant_id="t1", name="nota.txt", content_type="text/plain", data=data
    )
    second = await ingestor(repo).ingest(
        tenant_id="t1", name="nota.txt", content_type="text/plain", data=data
    )

    assert first.document_id == second.document_id
    assert len(await repo.list_documents(tenant_id="t1")) == 1


async def test_o_mesmo_arquivo_em_tenants_diferentes_sao_documentos_diferentes() -> None:
    repo = InMemoryChunkRepository()
    data = b"conteudo compartilhado"

    await ingestor(repo).ingest(
        tenant_id="t1", name="nota.txt", content_type="text/plain", data=data
    )
    await ingestor(repo).ingest(
        tenant_id="t2", name="nota.txt", content_type="text/plain", data=data
    )

    assert len(await repo.list_documents(tenant_id="t1")) == 1
    assert len(await repo.list_documents(tenant_id="t2")) == 1


async def test_arquivo_acima_do_teto_e_recusado_antes_de_extrair() -> None:
    repo = InMemoryChunkRepository()

    with pytest.raises(DocumentTooLarge):
        await ingestor(repo, max_bytes=10).ingest(
            tenant_id="t1", name="grande.txt", content_type="text/plain", data=b"x" * 11
        )

    assert await repo.list_documents(tenant_id="t1") == []


async def test_formato_desconhecido_e_recusado() -> None:
    repo = InMemoryChunkRepository()

    with pytest.raises(UnsupportedDocument):
        await ingestor(repo).ingest(
            tenant_id="t1",
            name="planilha.xlsx",
            content_type="application/vnd.ms-excel",
            data=b"qualquer",
        )


async def test_documento_sem_texto_e_recusado_e_nada_e_gravado() -> None:
    repo = InMemoryChunkRepository()

    with pytest.raises(EmptyDocument):
        await ingestor(repo).ingest(
            tenant_id="t1", name="vazio.txt", content_type="text/plain", data=b"   \n\n  "
        )

    assert await repo.list_documents(tenant_id="t1") == []

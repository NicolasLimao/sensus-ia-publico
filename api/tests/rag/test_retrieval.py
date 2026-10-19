"""Recuperação híbrida sobre o repositório em memória — sem banco, sem rede."""

from sensus_rag.contract import EmbeddedChunk, TextChunk
from sensus_rag.embedding.fake import FakeEmbedder
from sensus_rag.repository.memory import InMemoryChunkRepository
from sensus_rag.retrieval import HybridRetriever

EMBEDDER = FakeEmbedder(dimensions=16)


async def seed(repo: InMemoryChunkRepository, tenant: str, name: str, texts: list[str]) -> str:
    vectors = await EMBEDDER.embed_documents(texts)
    chunks = [
        EmbeddedChunk(
            chunk=TextChunk(content=text, index=i, start=0, end=len(text)),
            embedding=vector,
        )
        for i, (text, vector) in enumerate(zip(texts, vectors, strict=True))
    ]
    return await repo.upsert_document(
        tenant_id=tenant,
        name=name,
        content_type="text/plain",
        content_hash=f"hash-{name}",
        raw_text="\n\n".join(texts),
        chunks=chunks,
    )


async def test_encontra_o_trecho_pela_perna_lexical() -> None:
    repo = InMemoryChunkRepository()
    await seed(
        repo,
        "t1",
        "manual.txt",
        ["O prazo de reembolso e de 30 dias.", "O horario de atendimento e comercial."],
    )
    retriever = HybridRetriever(repository=repo, embedder=EMBEDDER, candidates=10, rrf_k=60)

    result = await retriever.search(tenant_id="t1", question="prazo de reembolso", top_k=1)

    assert "30 dias" in result[0].content


async def test_encontra_o_trecho_identico_pela_perna_semantica() -> None:
    repo = InMemoryChunkRepository()
    await seed(repo, "t1", "manual.txt", ["texto alfa", "texto beta"])
    retriever = HybridRetriever(repository=repo, embedder=EMBEDDER, candidates=10, rrf_k=60)

    result = await retriever.search(tenant_id="t1", question="texto beta", top_k=1)

    assert result[0].content == "texto beta"


async def test_nenhum_resultado_atravessa_de_outro_tenant() -> None:
    repo = InMemoryChunkRepository()
    await seed(repo, "t1", "a.txt", ["O prazo de reembolso e de 30 dias."])
    await seed(repo, "t2", "b.txt", ["O prazo de reembolso e de 90 dias."])
    retriever = HybridRetriever(repository=repo, embedder=EMBEDDER, candidates=10, rrf_k=60)

    result = await retriever.search(tenant_id="t1", question="prazo de reembolso", top_k=10)

    assert [r.document_name for r in result] == ["a.txt"]
    assert all("90 dias" not in r.content for r in result)


async def test_base_vazia_devolve_lista_vazia() -> None:
    retriever = HybridRetriever(
        repository=InMemoryChunkRepository(), embedder=EMBEDDER, candidates=10, rrf_k=60
    )

    assert await retriever.search(tenant_id="t1", question="qualquer", top_k=5) == []


async def test_listagem_nao_atravessa_de_outro_tenant() -> None:
    repo = InMemoryChunkRepository()
    await seed(repo, "t1", "a.txt", ["um"])
    await seed(repo, "t2", "b.txt", ["um"])

    documents_t1 = await repo.list_documents(tenant_id="t1")
    documents_t2 = await repo.list_documents(tenant_id="t2")

    assert [d.name for d in documents_t1] == ["a.txt"]
    assert [d.name for d in documents_t2] == ["b.txt"]


async def test_reupload_do_mesmo_hash_nao_duplica() -> None:
    repo = InMemoryChunkRepository()
    await seed(repo, "t1", "manual.txt", ["um", "dois"])
    await seed(repo, "t1", "manual.txt", ["um", "dois"])

    documents = await repo.list_documents(tenant_id="t1")

    assert len(documents) == 1
    assert documents[0].chunk_count == 2


async def test_apagar_documento_remove_os_chunks() -> None:
    repo = InMemoryChunkRepository()
    document_id = await seed(repo, "t1", "manual.txt", ["um", "dois"])

    assert await repo.delete_document(tenant_id="t1", document_id=document_id) is True
    assert await repo.list_documents(tenant_id="t1") == []


async def test_apagar_documento_de_outro_tenant_nao_encontra() -> None:
    repo = InMemoryChunkRepository()
    document_id = await seed(repo, "t1", "manual.txt", ["um"])

    assert await repo.delete_document(tenant_id="t2", document_id=document_id) is False
    assert len(await repo.list_documents(tenant_id="t1")) == 1

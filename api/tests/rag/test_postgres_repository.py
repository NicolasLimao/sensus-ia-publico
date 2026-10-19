"""Integração contra o Supabase real. Fora da suíte padrão: exige rede e credencial.

Rodar com:  SENSUS_DATABASE_URL=... uv run pytest -m integration -v
"""

import uuid
from collections.abc import AsyncIterator

import pytest
from psycopg_pool import AsyncConnectionPool

from sensus_rag.contract import EmbeddedChunk, TextChunk
from sensus_rag.embedding.fake import FakeEmbedder
from sensus_rag.migrate import apply_migrations
from sensus_rag.repository.postgres import PostgresChunkRepository
from sensus_rag.tenant_scope import TenantScope

pytestmark = pytest.mark.integration

DIMENSIONS = 1024
EMBEDDER = FakeEmbedder(dimensions=DIMENSIONS)


@pytest.fixture
async def pool(database_url: str) -> AsyncIterator[AsyncConnectionPool]:
    # A URL vem da fixture `database_url`, que lê as MESMAS settings da aplicação —
    # inclusive o `api/.env`. Lendo de `os.environ`, estes testes pulavam numa máquina
    # que ESTÁ configurada, e um teste de integração que nunca roda não guarda nada.
    await apply_migrations(database_url)
    async with AsyncConnectionPool(database_url, open=False) as p:
        await p.open()
        yield p


@pytest.fixture
def repo(pool: AsyncConnectionPool) -> PostgresChunkRepository:
    return PostgresChunkRepository(scope=TenantScope(pool=pool), dimensions=DIMENSIONS)


@pytest.fixture
async def tenants(pool: AsyncConnectionPool) -> AsyncIterator[tuple[str, str]]:
    """Dois tenants de verdade. Depois da migração 003 o `tenant_id` é uuid com chave
    estrangeira para `organizations`, e texto livre como "test-a" deixou de existir."""
    ids: list[str] = []
    async with pool.connection() as connection:
        for nome in ("test-a", "test-b"):
            cursor = await connection.execute(
                "insert into organizations (slug, name) values (%s, %s) returning id",
                (f"{nome}-{uuid.uuid4().hex}", nome),
            )
            row = await cursor.fetchone()
            assert row is not None
            ids.append(str(row[0]))
    yield ids[0], ids[1]
    # Cada teste limpa o que semeou: o banco é compartilhado entre rodadas. Apagar a
    # organização leva documentos e chunks junto, por cascata.
    async with pool.connection() as connection:
        await connection.execute("delete from organizations where id = any(%s)", (ids,))


@pytest.fixture
def tenant_a(tenants: tuple[str, str]) -> str:
    return tenants[0]


@pytest.fixture
def tenant_b(tenants: tuple[str, str]) -> str:
    return tenants[1]


async def seed(repo: PostgresChunkRepository, tenant: str, name: str, texts: list[str]) -> str:
    vectors = await EMBEDDER.embed_documents(texts)
    chunks = [
        EmbeddedChunk(chunk=TextChunk(content=t, index=i, start=0, end=len(t)), embedding=v)
        for i, (t, v) in enumerate(zip(texts, vectors, strict=True))
    ]
    return await repo.upsert_document(
        tenant_id=tenant,
        name=name,
        content_type="text/plain",
        content_hash=f"hash-{tenant}-{name}",
        raw_text="\n\n".join(texts),
        chunks=chunks,
    )


async def test_grava_e_lista_documento(repo: PostgresChunkRepository, tenant_a: str) -> None:
    await seed(repo, tenant_a, "manual.txt", ["primeiro trecho", "segundo trecho"])

    documents = await repo.list_documents(tenant_id=tenant_a)

    assert len(documents) == 1
    assert documents[0].chunk_count == 2


async def test_busca_lexical_encontra_pelo_stemmer_portugues(
    repo: PostgresChunkRepository, tenant_a: str
) -> None:
    await seed(repo, tenant_a, "manual.txt", ["O prazo para reembolsos é de 30 dias."])

    # "reembolso" no singular tem de casar com "reembolsos" no texto.
    result = await repo.search_lexical(tenant_id=tenant_a, query="reembolso", limit=5)

    assert len(result) == 1
    assert "30 dias" in result[0].content


async def test_busca_semantica_devolve_o_chunk_mais_proximo(
    repo: PostgresChunkRepository, tenant_a: str
) -> None:
    await seed(repo, tenant_a, "manual.txt", ["texto alfa", "texto beta"])
    target = await EMBEDDER.embed_query("texto beta")

    result = await repo.search_semantic(tenant_id=tenant_a, embedding=target, limit=1)

    assert result[0].content == "texto beta"


async def test_reupload_do_mesmo_hash_substitui_os_chunks(
    repo: PostgresChunkRepository, tenant_a: str
) -> None:
    await seed(repo, tenant_a, "manual.txt", ["um", "dois"])
    await seed(repo, tenant_a, "manual.txt", ["um", "dois"])

    documents = await repo.list_documents(tenant_id=tenant_a)

    assert len(documents) == 1
    assert documents[0].chunk_count == 2


async def test_apagar_documento_remove_os_chunks_em_cascata(
    repo: PostgresChunkRepository, pool: AsyncConnectionPool, tenant_a: str
) -> None:
    document_id = await seed(repo, tenant_a, "manual.txt", ["um", "dois"])

    assert await repo.delete_document(tenant_id=tenant_a, document_id=document_id) is True

    async with pool.connection() as connection:
        cursor = await connection.execute(
            "select count(*) from chunks where document_id = %s", (document_id,)
        )
        row = await cursor.fetchone()
    assert row is not None and row[0] == 0


async def test_nenhuma_busca_atravessa_entre_tenants(
    repo: PostgresChunkRepository, tenant_a: str, tenant_b: str
) -> None:
    """O risco nº 1 do SDD. Dois tenants, conteúdo quase idêntico, zero vazamento."""
    await seed(repo, tenant_a, "a.txt", ["O prazo de reembolso é de 30 dias."])
    await seed(repo, tenant_b, "b.txt", ["O prazo de reembolso é de 90 dias."])
    target = await EMBEDDER.embed_query("O prazo de reembolso é de 90 dias.")

    lexical = await repo.search_lexical(tenant_id=tenant_a, query="reembolso", limit=10)
    semantic = await repo.search_semantic(tenant_id=tenant_a, embedding=target, limit=10)
    listing = await repo.list_documents(tenant_id=tenant_a)

    assert all("90 dias" not in r.content for r in lexical + semantic)
    assert [d.name for d in listing] == ["a.txt"]


async def test_apagar_documento_de_outro_tenant_nao_encontra(
    repo: PostgresChunkRepository, tenant_a: str, tenant_b: str
) -> None:
    document_id = await seed(repo, tenant_a, "a.txt", ["um"])

    assert await repo.delete_document(tenant_id=tenant_b, document_id=document_id) is False
    assert len(await repo.list_documents(tenant_id=tenant_a)) == 1

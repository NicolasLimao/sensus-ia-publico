"""RLS só conta como ligada se o Postgres real recusar a leitura cruzada.

As DUAS metades, sempre: o outro tenant não vê E o dono continua vendo. Só a metade
negativa é satisfeita por um bug que devolva lista vazia para todo mundo — e é
exatamente esse o bug que a RLS pode introduzir.
"""

import uuid

import pytest
from psycopg_pool import AsyncConnectionPool

from sensus_rag.contract import EmbeddedChunk, TextChunk
from sensus_rag.repository.postgres import PostgresChunkRepository
from sensus_rag.tenant_scope import TenantScope

pytestmark = pytest.mark.integration

DIMENSOES = 1024


def _chunk(texto: str) -> EmbeddedChunk:
    return EmbeddedChunk(
        chunk=TextChunk(content=texto, index=0, start=0, end=len(texto)),
        embedding=(0.1,) * DIMENSOES,
    )


async def _organizacao(pool: AsyncConnectionPool, nome: str) -> str:
    async with pool.connection() as connection:
        cursor = await connection.execute(
            "insert into organizations (slug, name) values (%s, %s) returning id",
            (f"{nome}-{uuid.uuid4().hex}", nome),
        )
        row = await cursor.fetchone()
    assert row is not None
    return str(row[0])


async def test_um_tenant_nao_le_o_documento_do_outro(app_database_url: str) -> None:
    async with AsyncConnectionPool(app_database_url, open=False) as pool:
        await pool.open()
        repository = PostgresChunkRepository(scope=TenantScope(pool=pool), dimensions=DIMENSOES)
        dono = await _organizacao(pool, "dono")
        vizinho = await _organizacao(pool, "vizinho")

        await repository.upsert_document(
            tenant_id=dono,
            name="segredo.md",
            content_type="text/markdown",
            content_hash=uuid.uuid4().hex,
            raw_text="conteúdo do dono",
            chunks=[_chunk("conteúdo do dono")],
        )

        # Metade negativa: o vizinho não vê.
        assert await repository.list_documents(tenant_id=vizinho) == []
        # Metade positiva: o dono continua vendo. Sem ela, uma RLS que negasse tudo
        # passaria neste teste.
        assert len(await repository.list_documents(tenant_id=dono)) == 1


async def test_a_busca_semantica_tambem_respeita_a_policy(app_database_url: str) -> None:
    async with AsyncConnectionPool(app_database_url, open=False) as pool:
        await pool.open()
        repository = PostgresChunkRepository(scope=TenantScope(pool=pool), dimensions=DIMENSOES)
        dono = await _organizacao(pool, "dono")
        vizinho = await _organizacao(pool, "vizinho")
        await repository.upsert_document(
            tenant_id=dono,
            name="segredo.md",
            content_type="text/markdown",
            content_hash=uuid.uuid4().hex,
            raw_text="conteúdo do dono",
            chunks=[_chunk("conteúdo do dono")],
        )

        alheios = await repository.search_semantic(
            tenant_id=vizinho, embedding=(0.1,) * DIMENSOES, limit=10
        )
        proprios = await repository.search_semantic(
            tenant_id=dono, embedding=(0.1,) * DIMENSOES, limit=10
        )

    assert alheios == []
    assert len(proprios) == 1


async def test_a_conexao_da_aplicacao_nao_ignora_rls(app_database_url: str) -> None:
    """Se a aplicação estivesse conectando como `postgres`, todo teste acima passaria
    por acidente — superusuário ignora policy. Este afirma o papel."""
    async with AsyncConnectionPool(app_database_url, open=False) as pool:
        await pool.open()
        async with pool.connection() as connection:
            cursor = await connection.execute(
                "select rolsuper or rolbypassrls from pg_roles where rolname = current_user"
            )
            row = await cursor.fetchone()

    assert row is not None
    assert row[0] is False, "a aplicação conecta com papel que ignora RLS"


async def test_sem_contexto_de_tenant_a_aplicacao_nao_ve_nada(app_database_url: str) -> None:
    """O teste que de fato prova a policy.

    Os outros três passariam mesmo sem RLS: o repositório já carrega `where tenant_id
    = %s` em toda consulta, então o vizinho não veria nada de qualquer jeito. O que a
    policy acrescenta é o que acontece quando ALGUÉM ESQUECE o `where` — uma consulta
    crua, fora do `TenantScope`, sem GUC nenhum. `current_setting(..., true)` devolve
    nulo, a comparação não casa, e o resultado é zero linhas em vez das linhas de todo
    mundo. Falha fechada.
    """
    async with AsyncConnectionPool(app_database_url, open=False) as pool:
        await pool.open()
        dono = await _organizacao(pool, "dono")
        repository = PostgresChunkRepository(scope=TenantScope(pool=pool), dimensions=DIMENSOES)
        await repository.upsert_document(
            tenant_id=dono,
            name="segredo.md",
            content_type="text/markdown",
            content_hash=uuid.uuid4().hex,
            raw_text="conteúdo do dono",
            chunks=[_chunk("conteúdo do dono")],
        )

        # Consulta crua: sem escopo, sem `where`, sem contexto.
        async with pool.connection() as connection:
            cursor = await connection.execute("select count(*) from documents")
            sem_contexto = await cursor.fetchone()

        # A metade positiva: com contexto, o dono continua enxergando o próprio.
        com_contexto = len(await repository.list_documents(tenant_id=dono))

    assert sem_contexto is not None
    assert sem_contexto[0] == 0, "consulta sem contexto de tenant enxergou linhas"
    assert com_contexto == 1

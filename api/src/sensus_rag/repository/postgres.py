"""Persistência e busca no Postgres com pgvector.

Toda consulta filtra por `tenant_id` na cláusula, sempre como parâmetro — nunca por
interpolação. O isolamento entre tenants é o risco nº 1 do produto, e aqui é onde ele
se decide.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pgvector.psycopg import register_vector_async
from psycopg import AsyncConnection

from sensus_rag.contract import DocumentSummary, EmbeddedChunk, ScoredChunk, Vector
from sensus_rag.errors import RepositoryUnavailable
from sensus_rag.tenant_scope import TenantScope

_LIST = """
select d.id, d.name, count(c.id), d.created_at
from documents d
left join chunks c on c.document_id = d.id and c.tenant_id = d.tenant_id
where d.tenant_id = %s
group by d.id, d.name, d.created_at
order by d.created_at desc
"""

_SEMANTIC = """
select c.id, c.document_id, d.name, c.content, 1 - (c.embedding <=> %s::vector) as score
from chunks c
join documents d on d.id = c.document_id and d.tenant_id = c.tenant_id
where c.tenant_id = %s
order by c.embedding <=> %s::vector
limit %s
"""

_LEXICAL = """
select c.id, c.document_id, d.name, c.content,
       ts_rank(c.tsv, websearch_to_tsquery('portuguese', %s)) as score
from chunks c
join documents d on d.id = c.document_id and d.tenant_id = c.tenant_id
where c.tenant_id = %s
  and c.tsv @@ websearch_to_tsquery('portuguese', %s)
order by score desc
limit %s
"""


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


@dataclass(frozen=True)
class PostgresChunkRepository:
    scope: TenantScope
    dimensions: int

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
        try:
            # Uma transação só: ou o documento inteiro entra, ou nada entra. Base
            # meio indexada envenena a medição sem dar sinal. Quem a abre é o
            # `TenantScope` — aninhar outra aqui só criaria um savepoint sem uso.
            async with self.scope.connection(tenant_id) as connection:
                await self._prepare(connection)
                cursor = await connection.execute(
                    """
                    insert into documents
                        (tenant_id, name, content_type, content_hash, raw_text)
                    values (%s, %s, %s, %s, %s)
                    on conflict (tenant_id, content_hash) do update
                        set name = excluded.name,
                            content_type = excluded.content_type,
                            raw_text = excluded.raw_text
                    returning id
                    """,
                    (tenant_id, name, content_type, content_hash, raw_text),
                )
                row = await cursor.fetchone()
                assert row is not None
                document_id = str(row[0])

                await connection.execute(
                    "delete from chunks where tenant_id = %s and document_id = %s",
                    (tenant_id, document_id),
                )
                for item in chunks:
                    await connection.execute(
                        """
                        insert into chunks
                            (tenant_id, document_id, content, chunk_index, embedding)
                        values (%s, %s, %s, %s, %s)
                        """,
                        (
                            tenant_id,
                            document_id,
                            item.chunk.content,
                            item.chunk.index,
                            list(item.embedding),
                        ),
                    )
                return document_id
        except Exception as exc:
            raise RepositoryUnavailable("falha ao gravar o documento") from exc

    async def list_documents(self, *, tenant_id: str) -> list[DocumentSummary]:
        try:
            async with self.scope.connection(tenant_id) as connection:
                cursor = await connection.execute(_LIST, (tenant_id,))
                rows = await cursor.fetchall()
        except Exception as exc:
            raise RepositoryUnavailable("falha ao listar documentos") from exc
        return [
            DocumentSummary(id=str(row[0]), name=row[1], chunk_count=int(row[2]), created_at=row[3])
            for row in rows
        ]

    async def delete_document(self, *, tenant_id: str, document_id: str) -> bool:
        if not _is_uuid(document_id):
            # Id que não é UUID não existe em lugar nenhum: isso é "não encontrado",
            # não "banco indisponível". Sem esta guarda o Postgres levanta
            # `invalid input syntax for type uuid`, que o `except` abaixo converteria
            # em RepositoryUnavailable e a borda mostraria como 503 — enquanto o duplo
            # em memória devolve False e a borda responde 404. Ver
            # `tests/rag/test_repository_contract.py`.
            return False
        try:
            async with self.scope.connection(tenant_id) as connection:
                cursor = await connection.execute(
                    "delete from documents where tenant_id = %s and id = %s",
                    (tenant_id, document_id),
                )
                return cursor.rowcount > 0
        except Exception as exc:
            raise RepositoryUnavailable("falha ao apagar o documento") from exc

    async def search_semantic(
        self, *, tenant_id: str, embedding: Vector, limit: int
    ) -> list[ScoredChunk]:
        vector = list(embedding)
        try:
            async with self.scope.connection(tenant_id) as connection:
                await self._prepare(connection)
                cursor = await connection.execute(_SEMANTIC, (vector, tenant_id, vector, limit))
                return self._score_rows(await cursor.fetchall())
        except Exception as exc:
            raise RepositoryUnavailable("falha na busca semântica") from exc

    async def search_lexical(self, *, tenant_id: str, query: str, limit: int) -> list[ScoredChunk]:
        try:
            async with self.scope.connection(tenant_id) as connection:
                cursor = await connection.execute(_LEXICAL, (query, tenant_id, query, limit))
                return self._score_rows(await cursor.fetchall())
        except Exception as exc:
            raise RepositoryUnavailable("falha na busca lexical") from exc

    async def _prepare(self, connection: AsyncConnection[Any]) -> None:
        """Ensina o psycopg a converter `vector` nos dois sentidos."""
        await register_vector_async(connection)

    def _score_rows(self, rows: Sequence[tuple[Any, ...]]) -> list[ScoredChunk]:
        return [
            ScoredChunk(
                chunk_id=str(row[0]),
                document_id=str(row[1]),
                document_name=str(row[2]),
                content=str(row[3]),
                score=float(row[4]),
            )
            for row in rows
        ]

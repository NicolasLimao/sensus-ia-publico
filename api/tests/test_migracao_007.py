"""O que a 007 promete ao banco, verificado no banco.

Os privilégios são o ponto: as funções nascem com `anon`, `authenticated` e
`service_role` na ACL do Supabase, e `revoke from public` NÃO os remove. Uma fresta que
TROCA SENHA alcançável por `anon` é conta de qualquer pessoa — o ritual só vale se for
verificado.
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg_pool import AsyncConnectionPool

pytestmark = pytest.mark.integration

FUNCOES = [
    "auth_request_password_reset(text, text, timestamptz, integer)",
    "auth_password_reset_by_token_hash(text)",
    "auth_consume_password_reset(text, text)",
]


@pytest.fixture
async def pool(app_database_url: str) -> AsyncIterator[AsyncConnectionPool]:
    """Pela fixture `app_database_url` — o papel `sensus_app`, SEM bypass de RLS — e
    NUNCA por `os.environ`: as credenciais vivem no `api/.env`, lido por
    pydantic-settings e não exportado para o processo. Um teste que lesse o ambiente
    pularia numa máquina que ESTÁ configurada.
    """
    async with AsyncConnectionPool(app_database_url, open=False) as pool:
        await pool.open()
        yield pool


class TestMigracao007:
    async def test_a_tabela_existe(self, pool: AsyncConnectionPool) -> None:
        async with pool.connection() as connection:
            cursor = await connection.execute("select to_regclass('public.password_resets')")
            row = await cursor.fetchone()
        assert row is not None and row[0] is not None

    async def test_a_rls_esta_ligada(self, pool: AsyncConnectionPool) -> None:
        async with pool.connection() as connection:
            cursor = await connection.execute(
                "select relrowsecurity from pg_class where relname = 'password_resets'"
            )
            row = await cursor.fetchone()
        assert row is not None and row[0] is True

    async def test_a_aplicacao_nao_tem_privilegio_na_tabela(
        self, pool: AsyncConnectionPool
    ) -> None:
        """Diferente da 006 de propósito: não há lado com tenant, então não há policy —
        e sem `grant` a tabela é inalcançável pela aplicação. Só as frestas a tocam."""
        async with pool.connection() as connection:
            for privilegio in ("select", "insert", "update", "delete"):
                cursor = await connection.execute(
                    "select has_table_privilege('sensus_app', 'password_resets', %s)",
                    (privilegio,),
                )
                row = await cursor.fetchone()
                assert row is not None and row[0] is False, privilegio

    async def test_a_aplicacao_executa_as_tres_frestas(self, pool: AsyncConnectionPool) -> None:
        async with pool.connection() as connection:
            for funcao in FUNCOES:
                cursor = await connection.execute(
                    "select has_function_privilege('sensus_app', %s, 'execute')",
                    (funcao,),
                )
                row = await cursor.fetchone()
                assert row is not None and row[0] is True, funcao

    async def test_nenhum_papel_anonimo_executa_as_frestas(self, pool: AsyncConnectionPool) -> None:
        async with pool.connection() as connection:
            for papel in ("anon", "authenticated", "service_role", "public"):
                if papel != "public":
                    cursor = await connection.execute(
                        "select 1 from pg_roles where rolname = %s", (papel,)
                    )
                    if await cursor.fetchone() is None:
                        continue  # papel do Supabase, ausente num Postgres nu
                for funcao in FUNCOES:
                    cursor = await connection.execute(
                        "select has_function_privilege(%s, %s, 'execute')",
                        (papel, funcao),
                    )
                    row = await cursor.fetchone()
                    assert row is not None and row[0] is False, f"{papel} / {funcao}"


class TestSessionEpochVemDaColuna:
    """Conecta como DONA das tabelas, e não como `sensus_app` — trocar o default de uma
    coluna exige ser dona, e o resto deste arquivo usa de propósito o papel da
    aplicação. A diferença é deliberada e está aqui para não virar dúvida depois.
    """

    async def test_o_consumo_le_a_coluna_em_vez_de_somar_sobre_um_literal(
        self, database_url: str
    ) -> None:
        """Mesmo truque que a 3c usou em `test_migrations_identidade.py`: `session_epoch
        + 1` sobre um default 0 acerta por COINCIDÊNCIA, e nenhum teste normal distingue
        "leu a coluna" de "somou sobre um literal".

        Trocar o default DENTRO de uma transação revertida quebra a coincidência: lendo
        a coluna, o resultado é 8; sobre um literal, seria 1.
        """
        email = f"epoch-{uuid.uuid4().hex}@exemplo.invalido"

        async with (
            await psycopg.AsyncConnection.connect(database_url) as connection,
            connection.transaction(force_rollback=True),
        ):
            await connection.execute("alter table users alter column session_epoch set default 7")
            await connection.execute(
                "select * from auth_create_organization_with_admin(%s, %s, %s)",
                ("Acme", email, "antiga"),
            )
            token_hash = uuid.uuid4().hex
            await connection.execute(
                "select * from auth_request_password_reset(%s, %s, %s, %s)",
                (email, token_hash, datetime.now(UTC) + timedelta(hours=1), 120),
            )

            cursor = await connection.execute(
                "select * from auth_consume_password_reset(%s, %s)",
                (token_hash, "nova"),
            )
            row = await cursor.fetchone()

        assert row is not None
        assert row[5] == 8, "a funcao soma sobre um literal em vez de ler a coluna"

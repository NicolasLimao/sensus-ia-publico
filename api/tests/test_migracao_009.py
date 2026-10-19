"""O que a 009 promete ao banco, verificado no banco.

Os privilégios são o ponto, como na 007: as funções nascem com `anon`, `authenticated`
e `service_role` na ACL do Supabase, e `revoke from public` NÃO os remove. Uma fresta
que CRIA CONTA ADMIN alcançável por `anon` é organização de qualquer pessoa.
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg_pool import AsyncConnectionPool

pytestmark = pytest.mark.integration

FUNCOES = [
    "auth_request_signup(text, text, text, timestamptz, integer)",
    "auth_pending_signup_by_token_hash(text)",
    "auth_confirm_signup(text, text)",
]


def _email(prefixo: str) -> str:
    return f"{prefixo}-{uuid.uuid4().hex}@exemplo.invalido"


@pytest.fixture
async def pool(app_database_url: str) -> AsyncIterator[AsyncConnectionPool]:
    """Pela fixture `app_database_url` — o papel `sensus_app`, SEM bypass de RLS. Pelo
    dono das tabelas, um `grant execute` esquecido só apareceria em produção, e o teste
    da tabela inalcançável passaria pelo motivo errado."""
    async with AsyncConnectionPool(app_database_url, open=False) as pool:
        await pool.open()
        yield pool


class TestMigracao009:
    async def test_a_tabela_existe(self, pool: AsyncConnectionPool) -> None:
        async with pool.connection() as connection:
            cursor = await connection.execute("select to_regclass('public.pending_signups')")
            row = await cursor.fetchone()
        assert row is not None and row[0] is not None

    async def test_a_rls_esta_ligada(self, pool: AsyncConnectionPool) -> None:
        async with pool.connection() as connection:
            cursor = await connection.execute(
                "select relrowsecurity from pg_class where relname = 'pending_signups'"
            )
            row = await cursor.fetchone()
        assert row is not None and row[0] is True

    async def test_a_aplicacao_nao_tem_privilegio_na_tabela(
        self, pool: AsyncConnectionPool
    ) -> None:
        """Como a 007 e diferente da 006: não há lado com tenant, então não há policy —
        e sem `grant` a tabela é inalcançável pela aplicação. Só as frestas a tocam."""
        async with pool.connection() as connection:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                await connection.execute("select * from pending_signups")

    async def test_a_aplicacao_executa_as_tres_frestas(self, pool: AsyncConnectionPool) -> None:
        async with pool.connection() as connection:
            for funcao in FUNCOES:
                cursor = await connection.execute(
                    "select has_function_privilege('sensus_app', %s, 'execute')",
                    (funcao,),
                )
                row = await cursor.fetchone()
                assert row is not None and row[0] is True, f"{funcao} sem grant"

    async def test_nenhum_papel_anonimo_executa_as_frestas(self, pool: AsyncConnectionPool) -> None:
        async with pool.connection() as connection:
            cursor = await connection.execute(
                "select rolname from pg_roles "
                "where rolname in ('anon', 'authenticated', 'service_role')"
            )
            papeis = [row[0] for row in await cursor.fetchall()]

            for funcao in FUNCOES:
                cursor = await connection.execute(
                    "select has_function_privilege('public', %s, 'execute')", (funcao,)
                )
                row = await cursor.fetchone()
                assert row is not None and row[0] is False, f"{funcao} aberta a PUBLIC"

                for papel in papeis:
                    cursor = await connection.execute(
                        "select has_function_privilege(%s, %s, 'execute')",
                        (papel, funcao),
                    )
                    row = await cursor.fetchone()
                    assert row is not None and row[0] is False, (
                        f"{funcao} continua executável por {papel}"
                    )

    async def test_o_confirm_cria_organizacao_e_admin(self, pool: AsyncConnectionPool) -> None:
        """Seis colunas na ordem de `account_from_row`: id, tenant_id, email,
        password_hash, role, session_epoch. Índice posicional trocado é exatamente o
        que nenhum tipo pega."""
        endereco = _email("nova")
        token = uuid.uuid4().hex
        async with pool.connection() as connection:
            await connection.execute(
                "select * from auth_request_signup(%s, %s, %s, %s, %s)",
                ("Acme", endereco, token, datetime.now(UTC) + timedelta(days=1), 0),
            )
            cursor = await connection.execute(
                "select * from auth_confirm_signup(%s, %s)", (token, "hash-argon2")
            )
            row = await cursor.fetchone()

        assert row is not None
        assert row[2] == endereco
        assert row[3] == "hash-argon2"
        assert row[4] == "admin"
        assert row[5] == 0

    async def test_o_hash_do_token_e_unico_global(self, pool: AsyncConnectionPool) -> None:
        """Duas pendências com o mesmo resumo de token fariam a prévia responder sobre
        a errada. O índice único é quem garante."""
        token = uuid.uuid4().hex
        expira = datetime.now(UTC) + timedelta(days=1)
        async with pool.connection() as connection:
            await connection.execute(
                "select * from auth_request_signup(%s, %s, %s, %s, %s)",
                ("Acme", _email("um"), token, expira, 0),
            )
            with pytest.raises(psycopg.errors.UniqueViolation):
                await connection.execute(
                    "select * from auth_request_signup(%s, %s, %s, %s, %s)",
                    ("Globex", _email("dois"), token, expira, 0),
                )

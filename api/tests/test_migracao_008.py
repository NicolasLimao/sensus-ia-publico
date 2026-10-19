"""O que a 008 promete ao banco.

Os grants pesam mais aqui do que de costume: `auth_accept_invitation` muda de assinatura,
e assinatura nova significa `drop` + `create`. O `drop` ZERA a ACL — some o `sensus_app` e
reaparecem `anon`, `authenticated` e `service_role` pelo `alter default privileges` do
Supabase. Uma fresta que CRIA CONTA alcançável por `anon` é entrada livre na organização.
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg_pool import AsyncConnectionPool

pytestmark = pytest.mark.integration

# As duas frestas que a 008 faz nascer de um `drop`. As duas precisam do ritual de
# grants refeito, e as duas sao conferidas pelos mesmos dois testes.
FRESTAS = ["auth_accept_invitation(text, text)", "auth_invitation_by_token_hash(text)"]


@pytest.fixture
async def pool(app_database_url: str) -> AsyncIterator[AsyncConnectionPool]:
    """`sensus_app`, sem bypass de RLS — o papel que a aplicação usa."""
    async with AsyncConnectionPool(app_database_url, open=False) as pool:
        await pool.open()
        yield pool


class TestMigracao008:
    async def test_a_coluna_email_existe(self, pool: AsyncConnectionPool) -> None:
        async with pool.connection() as connection:
            cursor = await connection.execute(
                "select column_name from information_schema.columns "
                "where table_name = 'invitations' and column_name = 'email'"
            )
            assert await cursor.fetchone() is not None

    async def test_a_funcao_antiga_nao_existe_mais(self, pool: AsyncConnectionPool) -> None:
        """Mantê-la seria preservar o caminho sem vínculo — o modo inseguro que esta
        fatia existe para matar."""
        async with pool.connection() as connection:
            cursor = await connection.execute(
                "select 1 from pg_proc where proname = 'auth_accept_invitation' and pronargs = 3"
            )
            assert await cursor.fetchone() is None

    async def test_a_aplicacao_executa_as_frestas_novas(self, pool: AsyncConnectionPool) -> None:
        async with pool.connection() as connection:
            for fresta in FRESTAS:
                cursor = await connection.execute(
                    "select has_function_privilege('sensus_app', %s, 'execute')", (fresta,)
                )
                row = await cursor.fetchone()
                assert row is not None and row[0] is True, fresta

    async def test_nenhum_papel_anonimo_executa_as_frestas_novas(
        self, pool: AsyncConnectionPool
    ) -> None:
        """O teste que justifica a task: as duas funções nascem de um `drop`, e `drop`
        zera a ACL. Sem o ritual refeito, `anon` executaria uma fresta que CRIA CONTA."""
        async with pool.connection() as connection:
            for fresta in FRESTAS:
                for papel in ("anon", "authenticated", "service_role", "public"):
                    if papel != "public":
                        cursor = await connection.execute(
                            "select 1 from pg_roles where rolname = %s", (papel,)
                        )
                        if await cursor.fetchone() is None:
                            continue
                    cursor = await connection.execute(
                        "select has_function_privilege(%s, %s, 'execute')", (papel, fresta)
                    )
                    row = await cursor.fetchone()
                    assert row is not None and row[0] is False, f"{papel} / {fresta}"


class TestLimpezaDosLegados:
    """Conecta como DONA: precisa inserir linha com `email` nulo, que a borda nova não
    consegue mais produzir."""

    async def test_pendente_sem_email_e_revogado_e_os_outros_ficam_intactos(
        self, database_url: str
    ) -> None:
        async with (
            await psycopg.AsyncConnection.connect(database_url) as connection,
            connection.transaction(force_rollback=True),
        ):
            cursor = await connection.execute(
                "select * from auth_create_organization_with_admin(%s, %s, %s)",
                ("Legado", f"legado-{uuid.uuid4().hex}@exemplo.invalido", "hash"),
            )
            conta = await cursor.fetchone()
            assert conta is not None
            user_id, tenant_id = conta[0], conta[1]
            futuro = datetime.now(UTC) + timedelta(days=7)

            async def inserir(**carimbos: object) -> str:
                colunas = ", ".join(carimbos)
                valores = ", ".join(["%s"] * len(carimbos))
                cursor = await connection.execute(
                    f"insert into invitations "
                    f"(tenant_id, token_hash, created_by, expires_at"
                    f"{',' if colunas else ''} {colunas}) values (%s, %s, %s, %s"
                    f"{',' if colunas else ''} {valores}) returning id",
                    (tenant_id, uuid.uuid4().hex, user_id, futuro, *carimbos.values()),
                )
                row = await cursor.fetchone()
                assert row is not None
                return str(row[0])

            pendente_sem_email = await inserir()
            ja_aceito = await inserir(accepted_at=datetime.now(UTC))
            com_email = await inserir(email="alguem@exemplo.invalido")

            # A limpeza da 008, repetida aqui: a migração já rodou uma vez, e estas
            # linhas nasceram depois dela.
            await connection.execute(
                "update invitations set revoked_at = now() "
                "where email is null and accepted_at is null and revoked_at is null"
            )

            async def revogado(id_: str) -> bool:
                cursor = await connection.execute(
                    "select revoked_at is not null from invitations where id = %s", (id_,)
                )
                row = await cursor.fetchone()
                assert row is not None
                return bool(row[0])

            assert await revogado(pendente_sem_email) is True
            assert await revogado(ja_aceito) is False
            assert await revogado(com_email) is False

"""O diretório real, contra o Postgres. As MESMAS asserções do duplo, para que a
divergência entre os dois apareça aqui e não em produção.

Estes testes conectam pelo `app_database_url` — o papel `sensus_app`, SEM bypass de
RLS, que é o papel que a aplicação usa. Rodá-los pelo dono das tabelas, que ignora
policy, era testar o par real contra uma conexão que a produção não tem: o cadastro
passava aqui e estourava `InsufficientPrivilege` em produção.
"""

import uuid

import psycopg
import pytest
from psycopg_pool import AsyncConnectionPool

from sensus_api.users import EmailAlreadyUsed
from sensus_api.users_postgres import PostgresUserDirectory

pytestmark = pytest.mark.integration


async def test_cadastro_cria_organizacao_e_admin(app_database_url: str) -> None:
    email = f"ana-{uuid.uuid4().hex}@acme.com"
    async with AsyncConnectionPool(app_database_url, open=False) as pool:
        await pool.open()
        directory = PostgresUserDirectory(pool=pool)

        account = await directory.create_organization_with_admin(
            organization_name="Acme", email=email, password_hash="hash"
        )

        assert account.role == "admin"
        assert await directory.find_by_email(email) == account
        assert await directory.find_by_id(account.user_id) == account


async def test_email_repetido_e_recusado(app_database_url: str) -> None:
    email = f"ana-{uuid.uuid4().hex}@acme.com"
    async with AsyncConnectionPool(app_database_url, open=False) as pool:
        await pool.open()
        directory = PostgresUserDirectory(pool=pool)
        await directory.create_organization_with_admin(
            organization_name="Acme", email=email, password_hash="hash"
        )

        with pytest.raises(EmailAlreadyUsed):
            await directory.create_organization_with_admin(
                organization_name="Outra", email=email, password_hash="hash"
            )


async def test_cadastro_e_atomico(app_database_url: str, database_url: str) -> None:
    """Meia execução deixaria uma organização órfã, sem ninguém que consiga entrar
    nela — e ninguém perceberia, porque a organização não aparece em lugar nenhum."""
    email = f"ana-{uuid.uuid4().hex}@acme.com"
    async with AsyncConnectionPool(app_database_url, open=False) as pool:
        await pool.open()
        directory = PostgresUserDirectory(pool=pool)
        await directory.create_organization_with_admin(
            organization_name="Acme", email=email, password_hash="hash"
        )
        with pytest.raises(EmailAlreadyUsed):
            await directory.create_organization_with_admin(
                organization_name="Fantasma", email=email, password_hash="hash"
            )

    async with await psycopg.AsyncConnection.connect(database_url) as connection:
        cursor = await connection.execute(
            "select count(*) from organizations where name = 'Fantasma'"
        )
        row = await cursor.fetchone()

    assert row is not None
    assert row[0] == 0, "organização órfã sobreviveu à falha do usuário"


async def test_conta_nova_comeca_com_contador_zerado(app_database_url: str) -> None:
    email = f"ana-{uuid.uuid4().hex}@acme.com"
    async with AsyncConnectionPool(app_database_url, open=False) as pool:
        await pool.open()
        directory = PostgresUserDirectory(pool=pool)

        account = await directory.create_organization_with_admin(
            organization_name="Acme", email=email, password_hash="hash"
        )

        assert account.session_epoch == 0


async def test_encerrar_sessoes_incrementa_e_as_duas_leituras_enxergam(
    app_database_url: str,
) -> None:
    """As MESMAS asserções do duplo, contra o Postgres. E pelo `sensus_app`: um
    `grant execute` faltando na função nova só apareceria em produção se este teste
    rodasse pelo dono das tabelas, que ignora privilégio de RLS."""
    email = f"ana-{uuid.uuid4().hex}@acme.com"
    async with AsyncConnectionPool(app_database_url, open=False) as pool:
        await pool.open()
        directory = PostgresUserDirectory(pool=pool)
        account = await directory.create_organization_with_admin(
            organization_name="Acme", email=email, password_hash="hash"
        )

        await directory.bump_session_epoch(account.user_id)

        por_id = await directory.find_by_id(account.user_id)
        por_email = await directory.find_by_email(email)
        assert por_id is not None and por_email is not None
        assert por_id.session_epoch == 1
        assert por_email.session_epoch == 1


async def test_encerrar_sessoes_de_quem_nao_existe_nao_estoura(
    app_database_url: str,
) -> None:
    async with AsyncConnectionPool(app_database_url, open=False) as pool:
        await pool.open()
        directory = PostgresUserDirectory(pool=pool)

        await directory.bump_session_epoch("00000000-0000-0000-0000-000000000000")

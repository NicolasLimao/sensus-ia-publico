"""O que a 010 promete ao banco, verificado no banco.

O isolamento pela policy e "uma ativa por tenant" já são exercitados pelo adaptador
(`test_ai_configs_postgres.py`), pelo papel de aplicação. O que sobra aqui é a promessa
que nenhum teste de comportamento cobre: **versões são imutáveis**, e quem garante isso
não é o código — é a ausência de `delete` no `grant`. Uma migração futura que concedesse
`delete` passaria despercebida, e o histórico de config deixaria de ser histórico.
"""

from collections.abc import AsyncIterator

import pytest
from psycopg_pool import AsyncConnectionPool

pytestmark = pytest.mark.integration


@pytest.fixture
async def pool(app_database_url: str) -> AsyncIterator[AsyncConnectionPool]:
    """Pelo papel `sensus_app`, SEM bypass de RLS: pelo dono das tabelas os privilégios
    não se aplicam, e o teste passaria sem provar nada."""
    async with AsyncConnectionPool(app_database_url, open=False) as pool:
        await pool.open()
        yield pool


class TestMigracao010:
    async def test_a_rls_esta_ligada(self, pool: AsyncConnectionPool) -> None:
        async with pool.connection() as connection:
            cursor = await connection.execute(
                "select relrowsecurity from pg_class where relname = 'ai_configs'"
            )
            row = await cursor.fetchone()
        assert row is not None and row[0] is True

    async def test_a_aplicacao_le_grava_e_atualiza(self, pool: AsyncConnectionPool) -> None:
        async with pool.connection() as connection:
            cursor = await connection.execute(
                """
                select has_table_privilege('sensus_app', 'ai_configs', 'select'),
                       has_table_privilege('sensus_app', 'ai_configs', 'insert'),
                       has_table_privilege('sensus_app', 'ai_configs', 'update')
                """
            )
            row = await cursor.fetchone()
        assert row == (True, True, True)

    async def test_a_aplicacao_NAO_apaga(self, pool: AsyncConnectionPool) -> None:
        """Salvar cria versão nova e ativar é rollback: apagar não é operação do
        produto. Sem `delete`, nem um bug consegue transformar o histórico em nada."""
        async with pool.connection() as connection:
            cursor = await connection.execute(
                "select has_table_privilege('sensus_app', 'ai_configs', 'delete')"
            )
            row = await cursor.fetchone()
        assert row is not None and row[0] is False

    async def test_o_indice_da_versao_ativa_e_unico_e_parcial(
        self, pool: AsyncConnectionPool
    ) -> None:
        """Quem garante uma ativa por tenant é o índice, não o código — e ele só vale
        se continuar `unique` e `where is_active`."""
        async with pool.connection() as connection:
            cursor = await connection.execute(
                "select indexdef from pg_indexes where indexname = %s",
                ("ai_configs_uma_ativa_por_tenant",),
            )
            row = await cursor.fetchone()
        assert row is not None
        definicao = row[0].lower()
        assert "unique" in definicao
        assert "where" in definicao and "is_active" in definicao

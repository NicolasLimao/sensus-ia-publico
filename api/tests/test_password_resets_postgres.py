"""O diretório real de resets, contra o Postgres.

As MESMAS asserções do duplo valem aqui, e mais três que o duplo acerta por construção
sem provar nada — são propriedades da TRANSAÇÃO, e são a razão de este arquivo existir:
o consumo concorrente e o freio concorrente.

Que o `session_epoch` venha da COLUNA, e não de uma constante, é propriedade da função
SQL e não do adaptador — fica em `test_migracao_007.py`, que conecta como dona das
tabelas (trocar o default exige isso).

Conecta pelo `sensus_app`, SEM bypass de RLS. Pelo dono das tabelas, um `grant execute`
esquecido nas frestas novas só apareceria em produção — e o teste da tabela inalcançável
passaria pelo motivo errado.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from psycopg_pool import AsyncConnectionPool

from sensus_api.password_resets_postgres import PostgresPasswordResetDirectory
from sensus_api.users import Account
from sensus_api.users_postgres import PostgresUserDirectory

pytestmark = pytest.mark.integration

UMA_HORA = 3600
DOIS_MINUTOS = 120


def _hash() -> str:
    return uuid.uuid4().hex


def _email(prefixo: str) -> str:
    return f"{prefixo}-{uuid.uuid4().hex}@exemplo.invalido"


def _daqui_a(segundos: int) -> datetime:
    return datetime.now(UTC) + timedelta(seconds=segundos)


@pytest.fixture
async def pool(app_database_url: str) -> AsyncIterator[AsyncConnectionPool]:
    """`sensus_app`, sem bypass de RLS — é o papel que a aplicação usa, e é o único que
    faz o teste da tabela inalcançável significar alguma coisa: pelo dono das tabelas, o
    `select` direto passaria e a ausência de grant nunca apareceria."""
    async with AsyncConnectionPool(app_database_url, open=False) as pool:
        await pool.open()
        yield pool


async def _conta(users: PostgresUserDirectory) -> Account:
    return await users.create_organization_with_admin(
        organization_name="Acme", email=_email("dona"), password_hash="antiga"
    )


class TestRequest:
    async def test_devolve_a_conta_e_o_email_do_banco(self, pool: AsyncConnectionPool) -> None:
        users = PostgresUserDirectory(pool=pool)
        resets = PostgresPasswordResetDirectory(pool=pool)
        conta = await _conta(users)

        pedido = await resets.request(
            email=conta.email.upper(),
            token_hash=_hash(),
            expires_at=_daqui_a(UMA_HORA),
            throttle_seconds=DOIS_MINUTOS,
        )

        assert pedido is not None
        assert pedido.user_id == conta.user_id
        assert pedido.email == conta.email

    async def test_email_sem_conta_devolve_none(self, pool: AsyncConnectionPool) -> None:
        resets = PostgresPasswordResetDirectory(pool=pool)

        assert (
            await resets.request(
                email=_email("ninguem"),
                token_hash=_hash(),
                expires_at=_daqui_a(UMA_HORA),
                throttle_seconds=DOIS_MINUTOS,
            )
            is None
        )

    async def test_segundo_pedido_dentro_da_janela_devolve_none(
        self, pool: AsyncConnectionPool
    ) -> None:
        users = PostgresUserDirectory(pool=pool)
        resets = PostgresPasswordResetDirectory(pool=pool)
        conta = await _conta(users)
        await resets.request(
            email=conta.email,
            token_hash=_hash(),
            expires_at=_daqui_a(UMA_HORA),
            throttle_seconds=DOIS_MINUTOS,
        )

        assert (
            await resets.request(
                email=conta.email,
                token_hash=_hash(),
                expires_at=_daqui_a(UMA_HORA),
                throttle_seconds=DOIS_MINUTOS,
            )
            is None
        )

    async def test_pedido_freado_nao_derruba_o_link_anterior(
        self, pool: AsyncConnectionPool
    ) -> None:
        """A ordem importa: substituir primeiro e ser freado depois deixaria a pessoa
        sem link nenhum válido."""
        users = PostgresUserDirectory(pool=pool)
        resets = PostgresPasswordResetDirectory(pool=pool)
        conta = await _conta(users)
        primeiro = _hash()
        await resets.request(
            email=conta.email,
            token_hash=primeiro,
            expires_at=_daqui_a(UMA_HORA),
            throttle_seconds=DOIS_MINUTOS,
        )

        await resets.request(
            email=conta.email,
            token_hash=_hash(),
            expires_at=_daqui_a(UMA_HORA),
            throttle_seconds=DOIS_MINUTOS,
        )

        carimbos = await resets.stamps_by_token_hash(primeiro)
        assert carimbos is not None and carimbos.superseded_at is None

    async def test_pedido_fora_da_janela_substitui_o_anterior(
        self, pool: AsyncConnectionPool
    ) -> None:
        users = PostgresUserDirectory(pool=pool)
        resets = PostgresPasswordResetDirectory(pool=pool)
        conta = await _conta(users)
        primeiro = _hash()
        await resets.request(
            email=conta.email,
            token_hash=primeiro,
            expires_at=_daqui_a(UMA_HORA),
            throttle_seconds=0,
        )

        await resets.request(
            email=conta.email,
            token_hash=_hash(),
            expires_at=_daqui_a(UMA_HORA),
            throttle_seconds=0,
        )

        carimbos = await resets.stamps_by_token_hash(primeiro)
        assert carimbos is not None and carimbos.superseded_at is not None

    async def test_dois_pedidos_SIMULTANEOS_geram_um_so(self, pool: AsyncConnectionPool) -> None:
        """A prova que o duplo não dá. Sem o `for update` na fresta, as duas transações
        avaliam o `not exists` no próprio snapshot, nenhuma enxerga o insert da outra e
        AS DUAS PASSAM — o freio sairia quebrado no único caso para o qual existe."""
        users = PostgresUserDirectory(pool=pool)
        resets = PostgresPasswordResetDirectory(pool=pool)
        conta = await _conta(users)

        resultados = await asyncio.gather(
            resets.request(
                email=conta.email,
                token_hash=_hash(),
                expires_at=_daqui_a(UMA_HORA),
                throttle_seconds=DOIS_MINUTOS,
            ),
            resets.request(
                email=conta.email,
                token_hash=_hash(),
                expires_at=_daqui_a(UMA_HORA),
                throttle_seconds=DOIS_MINUTOS,
            ),
        )

        assert sum(1 for r in resultados if r is not None) == 1


class TestConsume:
    async def test_troca_a_senha_e_incrementa_a_geracao(self, pool: AsyncConnectionPool) -> None:
        users = PostgresUserDirectory(pool=pool)
        resets = PostgresPasswordResetDirectory(pool=pool)
        conta = await _conta(users)
        token_hash = _hash()
        await resets.request(
            email=conta.email,
            token_hash=token_hash,
            expires_at=_daqui_a(UMA_HORA),
            throttle_seconds=DOIS_MINUTOS,
        )

        nova = await resets.consume(token_hash=token_hash, password_hash="nova")

        assert nova is not None
        assert nova.password_hash == "nova"
        assert nova.session_epoch == conta.session_epoch + 1

    async def test_dois_cliques_SIMULTANEOS_no_mesmo_link(self, pool: AsyncConnectionPool) -> None:
        """A segunda prova que o duplo não dá: um redefine, o outro não consome nada."""
        users = PostgresUserDirectory(pool=pool)
        resets = PostgresPasswordResetDirectory(pool=pool)
        conta = await _conta(users)
        token_hash = _hash()
        await resets.request(
            email=conta.email,
            token_hash=token_hash,
            expires_at=_daqui_a(UMA_HORA),
            throttle_seconds=DOIS_MINUTOS,
        )

        resultados = await asyncio.gather(
            resets.consume(token_hash=token_hash, password_hash="uma"),
            resets.consume(token_hash=token_hash, password_hash="outra"),
        )

        assert sum(1 for r in resultados if r is not None) == 1

    async def test_link_vencido_e_link_substituido_nao_consomem(
        self, pool: AsyncConnectionPool
    ) -> None:
        users = PostgresUserDirectory(pool=pool)
        resets = PostgresPasswordResetDirectory(pool=pool)
        conta = await _conta(users)
        vencido = _hash()
        await resets.request(
            email=conta.email,
            token_hash=vencido,
            expires_at=_daqui_a(-1),
            throttle_seconds=0,
        )
        substituido = _hash()
        await resets.request(
            email=conta.email,
            token_hash=substituido,
            expires_at=_daqui_a(UMA_HORA),
            throttle_seconds=0,
        )
        await resets.request(
            email=conta.email,
            token_hash=_hash(),
            expires_at=_daqui_a(UMA_HORA),
            throttle_seconds=0,
        )

        assert await resets.consume(token_hash=vencido, password_hash="n") is None
        assert await resets.consume(token_hash=substituido, password_hash="n") is None

    async def test_token_inexistente_devolve_none(self, pool: AsyncConnectionPool) -> None:
        resets = PostgresPasswordResetDirectory(pool=pool)

        assert await resets.consume(token_hash=_hash(), password_hash="n") is None


class TestStamps:
    async def test_token_inexistente_devolve_none(self, pool: AsyncConnectionPool) -> None:
        resets = PostgresPasswordResetDirectory(pool=pool)

        assert await resets.stamps_by_token_hash(_hash()) is None

    async def test_traz_o_email_do_dono(self, pool: AsyncConnectionPool) -> None:
        users = PostgresUserDirectory(pool=pool)
        resets = PostgresPasswordResetDirectory(pool=pool)
        conta = await _conta(users)
        token_hash = _hash()
        await resets.request(
            email=conta.email,
            token_hash=token_hash,
            expires_at=_daqui_a(UMA_HORA),
            throttle_seconds=DOIS_MINUTOS,
        )

        carimbos = await resets.stamps_by_token_hash(token_hash)

        assert carimbos is not None and carimbos.email == conta.email


class TestTabelaInalcancavel:
    async def test_select_direto_pela_aplicacao_e_recusado(self, pool: AsyncConnectionPool) -> None:
        """A terceira prova que só o banco dá: a tabela tem RLS ligada e NENHUM grant
        para `sensus_app`, então a aplicação não a lê nem com um pedido recém-criado.
        Quem a toca são as frestas, que rodam como dona."""
        users = PostgresUserDirectory(pool=pool)
        resets = PostgresPasswordResetDirectory(pool=pool)
        conta = await _conta(users)
        await resets.request(
            email=conta.email,
            token_hash=_hash(),
            expires_at=_daqui_a(UMA_HORA),
            throttle_seconds=DOIS_MINUTOS,
        )

        with pytest.raises(Exception) as erro:
            async with pool.connection() as connection:
                cursor = await connection.execute("select count(*) from password_resets")
                await cursor.fetchone()

        assert "permission denied" in str(erro.value).lower()

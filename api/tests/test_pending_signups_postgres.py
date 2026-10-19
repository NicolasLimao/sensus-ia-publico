"""O diretório real de pendências, contra o Postgres.

As MESMAS asserções do duplo valem aqui, e mais três que o duplo acerta por construção
sem provar nada — são propriedades da TRANSAÇÃO, e são a razão de este arquivo existir:
o freio concorrente, o consumo concorrente e o `23505` que desfaz o consumo junto.

Conecta pelo `sensus_app`, SEM bypass de RLS: pelo dono das tabelas, um `grant execute`
esquecido só apareceria em produção.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from psycopg_pool import AsyncConnectionPool

from sensus_api.pending_signups_postgres import PostgresPendingSignupDirectory
from sensus_api.users import EmailAlreadyUsed
from sensus_api.users_postgres import PostgresUserDirectory

pytestmark = pytest.mark.integration

UM_DIA = 86400
DOIS_MINUTOS = 120


def _hash() -> str:
    return uuid.uuid4().hex


def _email(prefixo: str) -> str:
    return f"{prefixo}-{uuid.uuid4().hex}@exemplo.invalido"


def _daqui_a(segundos: int) -> datetime:
    return datetime.now(UTC) + timedelta(seconds=segundos)


@pytest.fixture
async def pool(app_database_url: str) -> AsyncIterator[AsyncConnectionPool]:
    async with AsyncConnectionPool(app_database_url, open=False) as pool:
        await pool.open()
        yield pool


@pytest.fixture
def pendings(pool: AsyncConnectionPool) -> PostgresPendingSignupDirectory:
    return PostgresPendingSignupDirectory(pool=pool)


@pytest.fixture
def users(pool: AsyncConnectionPool) -> PostgresUserDirectory:
    return PostgresUserDirectory(pool=pool)


class TestRequest:
    async def test_endereco_livre_cria_a_pendencia(
        self, pendings: PostgresPendingSignupDirectory
    ) -> None:
        endereco = _email("nova")
        pedido = await pendings.request(
            organization_name="Acme",
            email=endereco,
            token_hash=_hash(),
            expires_at=_daqui_a(UM_DIA),
            throttle_seconds=DOIS_MINUTOS,
        )

        assert pedido is not None
        assert pedido.email == endereco
        assert pedido.already_has_account is False

    async def test_endereco_com_conta_cria_a_pendencia_e_avisa(
        self,
        pendings: PostgresPendingSignupDirectory,
        users: PostgresUserDirectory,
    ) -> None:
        endereco = _email("dona")
        await users.create_organization_with_admin(
            organization_name="Globex", email=endereco, password_hash="hash"
        )
        token = _hash()

        pedido = await pendings.request(
            organization_name="Acme",
            email=endereco,
            token_hash=token,
            expires_at=_daqui_a(UM_DIA),
            throttle_seconds=DOIS_MINUTOS,
        )

        assert pedido is not None
        assert pedido.already_has_account is True
        assert await pendings.stamps_by_token_hash(token) is not None

    async def test_dois_pedidos_SIMULTANEOS_ao_mesmo_endereco_so_um_passa(
        self, pendings: PostgresPendingSignupDirectory
    ) -> None:
        """O teste que só existe aqui. Sob READ COMMITTED, duas transações avaliando o
        `not exists` veem cada uma o próprio snapshot e as duas passariam — o freio
        sairia quebrado no único caso para o qual existe. Quem impede é o lock
        consultivo por endereço, e é isto que o prova."""
        endereco = _email("disputada")

        resultados = await asyncio.gather(
            pendings.request(
                organization_name="Acme",
                email=endereco,
                token_hash=_hash(),
                expires_at=_daqui_a(UM_DIA),
                throttle_seconds=DOIS_MINUTOS,
            ),
            pendings.request(
                organization_name="Acme",
                email=endereco,
                token_hash=_hash(),
                expires_at=_daqui_a(UM_DIA),
                throttle_seconds=DOIS_MINUTOS,
            ),
        )

        assert sum(1 for resultado in resultados if resultado is not None) == 1

    async def test_o_pedido_novo_substitui_o_anterior(
        self, pendings: PostgresPendingSignupDirectory
    ) -> None:
        endereco = _email("nova")
        primeiro = _hash()
        await pendings.request(
            organization_name="Acme",
            email=endereco,
            token_hash=primeiro,
            expires_at=_daqui_a(UM_DIA),
            throttle_seconds=0,
        )

        await pendings.request(
            organization_name="Acme",
            email=endereco,
            token_hash=_hash(),
            expires_at=_daqui_a(UM_DIA),
            throttle_seconds=0,
        )

        antigo = await pendings.stamps_by_token_hash(primeiro)
        assert antigo is not None and antigo.superseded_at is not None


class TestConfirm:
    async def test_confirmar_cria_organizacao_e_admin(
        self,
        pendings: PostgresPendingSignupDirectory,
        users: PostgresUserDirectory,
    ) -> None:
        endereco = _email("nova")
        token = _hash()
        await pendings.request(
            organization_name="Acme",
            email=endereco,
            token_hash=token,
            expires_at=_daqui_a(UM_DIA),
            throttle_seconds=DOIS_MINUTOS,
        )

        conta = await pendings.confirm(token_hash=token, password_hash="hash-argon2")

        assert conta is not None
        assert conta.email == endereco
        assert conta.role == "admin"
        assert conta.session_epoch == 0
        assert await users.find_by_email(endereco) is not None

    async def test_dois_cliques_SIMULTANEOS_criam_UMA_organizacao(
        self, pendings: PostgresPendingSignupDirectory
    ) -> None:
        """A outra propriedade que só a transação tem: o consumo acontece dentro do
        `update`, e quem perde a corrida reavalia o `where` na linha travada."""
        endereco = _email("nova")
        token = _hash()
        await pendings.request(
            organization_name="Acme",
            email=endereco,
            token_hash=token,
            expires_at=_daqui_a(UM_DIA),
            throttle_seconds=DOIS_MINUTOS,
        )

        resultados = await asyncio.gather(
            pendings.confirm(token_hash=token, password_hash="uma"),
            pendings.confirm(token_hash=token, password_hash="outra"),
            return_exceptions=True,
        )

        contas = [
            resultado
            for resultado in resultados
            if resultado is not None and not isinstance(resultado, BaseException)
        ]
        assert len(contas) == 1

    async def test_endereco_que_ganhou_conta_no_meio_tempo_levanta_e_NAO_consome(
        self,
        pendings: PostgresPendingSignupDirectory,
        users: PostgresUserDirectory,
    ) -> None:
        """O `23505` sobe de dentro da função aninhada e desfaz a transação inteira —
        inclusive o consumo. É o que garante que nada fique meio-feito."""
        endereco = _email("disputada")
        token = _hash()
        await pendings.request(
            organization_name="Acme",
            email=endereco,
            token_hash=token,
            expires_at=_daqui_a(UM_DIA),
            throttle_seconds=DOIS_MINUTOS,
        )
        await users.create_organization_with_admin(
            organization_name="Globex", email=endereco, password_hash="hash"
        )

        with pytest.raises(EmailAlreadyUsed):
            await pendings.confirm(token_hash=token, password_hash="hash-argon2")

        stamps = await pendings.stamps_by_token_hash(token)
        assert stamps is not None and stamps.consumed_at is None

    async def test_link_vencido_nao_confirma(
        self, pendings: PostgresPendingSignupDirectory
    ) -> None:
        token = _hash()
        await pendings.request(
            organization_name="Acme",
            email=_email("nova"),
            token_hash=token,
            expires_at=_daqui_a(-1),
            throttle_seconds=DOIS_MINUTOS,
        )

        assert await pendings.confirm(token_hash=token, password_hash="h") is None


class TestStamps:
    async def test_token_desconhecido_nao_tem_carimbos(
        self, pendings: PostgresPendingSignupDirectory
    ) -> None:
        assert await pendings.stamps_by_token_hash(_hash()) is None

    async def test_os_carimbos_trazem_nome_e_endereco(
        self, pendings: PostgresPendingSignupDirectory
    ) -> None:
        endereco = _email("nova")
        token = _hash()
        await pendings.request(
            organization_name="Acme",
            email=endereco,
            token_hash=token,
            expires_at=_daqui_a(UM_DIA),
            throttle_seconds=DOIS_MINUTOS,
        )

        stamps = await pendings.stamps_by_token_hash(token)

        assert stamps is not None
        assert stamps.organization_name == "Acme"
        assert stamps.email == endereco

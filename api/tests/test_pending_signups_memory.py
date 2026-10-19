"""O duplo do diretório de pendências de cadastro.

O que NÃO está aqui, e por quê: o freio concorrente e o consumo concorrente são
propriedades da TRANSAÇÃO, e o duplo os acerta por construção sem provar nada. Ficam
em `test_pending_signups_postgres.py`, contra o Postgres.
"""

from datetime import UTC, datetime, timedelta

import pytest

from sensus_api.pending_signups_memory import InMemoryPendingSignupDirectory
from sensus_api.users import EmailAlreadyUsed
from sensus_api.users_memory import InMemoryUserDirectory

LIVRE = "nova@exemplo.invalido"
DOIS_MINUTOS = 120
UM_DIA = 86400


def _daqui_a(segundos: int) -> datetime:
    return datetime.now(UTC) + timedelta(seconds=segundos)


@pytest.fixture
def users() -> InMemoryUserDirectory:
    return InMemoryUserDirectory()


@pytest.fixture
def pendings(users: InMemoryUserDirectory) -> InMemoryPendingSignupDirectory:
    return InMemoryPendingSignupDirectory(users=users)


class TestRequest:
    async def test_endereco_livre_cria_a_pendencia(
        self, pendings: InMemoryPendingSignupDirectory
    ) -> None:
        pedido = await pendings.request(
            organization_name="Acme",
            email=LIVRE,
            token_hash="hash-um",
            expires_at=_daqui_a(UM_DIA),
            throttle_seconds=DOIS_MINUTOS,
        )

        assert pedido is not None
        assert pedido.email == LIVRE
        assert pedido.already_has_account is False

    async def test_o_endereco_e_normalizado_para_minusculas(
        self, pendings: InMemoryPendingSignupDirectory
    ) -> None:
        """O e-mail devolvido é o que vai para o destinatário. Ecoar a caixa que o
        chamador digitou é o tipo de eco que vira vetor quando alguém a forja."""
        pedido = await pendings.request(
            organization_name="Acme",
            email="NOVA@Exemplo.Invalido",
            token_hash="hash-um",
            expires_at=_daqui_a(UM_DIA),
            throttle_seconds=DOIS_MINUTOS,
        )

        assert pedido is not None
        assert pedido.email == LIVRE

    async def test_endereco_com_conta_tambem_cria_a_pendencia(
        self, pendings: InMemoryPendingSignupDirectory, users: InMemoryUserDirectory
    ) -> None:
        """Decisão 6 da spec: sem linha não há sobre o que aplicar o freio, e a rota
        anônima viraria amplificador de e-mail contra um endereço conhecido."""
        await users.create_organization_with_admin(
            organization_name="Globex", email=LIVRE, password_hash="hash"
        )

        pedido = await pendings.request(
            organization_name="Acme",
            email=LIVRE,
            token_hash="hash-um",
            expires_at=_daqui_a(UM_DIA),
            throttle_seconds=DOIS_MINUTOS,
        )

        assert pedido is not None
        assert pedido.already_has_account is True
        assert await pendings.stamps_by_token_hash("hash-um") is not None

    async def test_pedido_dentro_da_janela_e_freado(
        self, pendings: InMemoryPendingSignupDirectory
    ) -> None:
        await pendings.request(
            organization_name="Acme",
            email=LIVRE,
            token_hash="hash-um",
            expires_at=_daqui_a(UM_DIA),
            throttle_seconds=DOIS_MINUTOS,
        )

        segundo = await pendings.request(
            organization_name="Acme",
            email=LIVRE,
            token_hash="hash-dois",
            expires_at=_daqui_a(UM_DIA),
            throttle_seconds=DOIS_MINUTOS,
        )

        assert segundo is None
        assert await pendings.stamps_by_token_hash("hash-dois") is None

    async def test_pedido_fora_da_janela_substitui_o_anterior(
        self, pendings: InMemoryPendingSignupDirectory
    ) -> None:
        await pendings.request(
            organization_name="Acme",
            email=LIVRE,
            token_hash="hash-um",
            expires_at=_daqui_a(UM_DIA),
            throttle_seconds=0,
        )

        segundo = await pendings.request(
            organization_name="Acme",
            email=LIVRE,
            token_hash="hash-dois",
            expires_at=_daqui_a(UM_DIA),
            throttle_seconds=0,
        )

        assert segundo is not None
        antigo = await pendings.stamps_by_token_hash("hash-um")
        assert antigo is not None and antigo.superseded_at is not None
        novo = await pendings.stamps_by_token_hash("hash-dois")
        assert novo is not None and novo.superseded_at is None


class TestConfirm:
    async def test_confirmar_cria_a_organizacao_e_o_admin(
        self, pendings: InMemoryPendingSignupDirectory, users: InMemoryUserDirectory
    ) -> None:
        await pendings.request(
            organization_name="Acme",
            email=LIVRE,
            token_hash="hash-um",
            expires_at=_daqui_a(UM_DIA),
            throttle_seconds=DOIS_MINUTOS,
        )

        conta = await pendings.confirm(token_hash="hash-um", password_hash="hash-senha")

        assert conta is not None
        assert conta.email == LIVRE
        assert conta.role == "admin"
        assert users.organization_names[conta.tenant_id] == "Acme"

    async def test_confirmar_duas_vezes_nao_cria_duas_organizacoes(
        self, pendings: InMemoryPendingSignupDirectory
    ) -> None:
        await pendings.request(
            organization_name="Acme",
            email=LIVRE,
            token_hash="hash-um",
            expires_at=_daqui_a(UM_DIA),
            throttle_seconds=DOIS_MINUTOS,
        )
        await pendings.confirm(token_hash="hash-um", password_hash="hash-senha")

        assert await pendings.confirm(token_hash="hash-um", password_hash="outra") is None

    async def test_link_vencido_nao_confirma(
        self, pendings: InMemoryPendingSignupDirectory
    ) -> None:
        await pendings.request(
            organization_name="Acme",
            email=LIVRE,
            token_hash="hash-um",
            expires_at=_daqui_a(-1),
            throttle_seconds=DOIS_MINUTOS,
        )

        assert await pendings.confirm(token_hash="hash-um", password_hash="h") is None

    async def test_token_desconhecido_nao_confirma(
        self, pendings: InMemoryPendingSignupDirectory
    ) -> None:
        assert await pendings.confirm(token_hash="nao-existe", password_hash="h") is None

    async def test_endereco_que_ganhou_conta_no_meio_tempo_levanta(
        self, pendings: InMemoryPendingSignupDirectory, users: InMemoryUserDirectory
    ) -> None:
        """O caso que o `23505` do par real produz: alguém aceitou um convite entre o
        pedido e o clique. A transação real desfaz TUDO, inclusive o consumo — e o
        duplo precisa desfazer à mão, ou mentiria sobre o estado da pendência."""
        await pendings.request(
            organization_name="Acme",
            email=LIVRE,
            token_hash="hash-um",
            expires_at=_daqui_a(UM_DIA),
            throttle_seconds=DOIS_MINUTOS,
        )
        await users.create_organization_with_admin(
            organization_name="Globex", email=LIVRE, password_hash="hash"
        )

        with pytest.raises(EmailAlreadyUsed):
            await pendings.confirm(token_hash="hash-um", password_hash="hash-senha")

        stamps = await pendings.stamps_by_token_hash("hash-um")
        assert stamps is not None and stamps.consumed_at is None


class TestStamps:
    async def test_token_desconhecido_nao_tem_carimbos(
        self, pendings: InMemoryPendingSignupDirectory
    ) -> None:
        assert await pendings.stamps_by_token_hash("nao-existe") is None

    async def test_os_carimbos_trazem_o_nome_da_organizacao(
        self, pendings: InMemoryPendingSignupDirectory
    ) -> None:
        """A prévia mostra o nome na TELA — é lá que ele aparece, e não no e-mail
        (decisão 5 da spec)."""
        await pendings.request(
            organization_name="Acme",
            email=LIVRE,
            token_hash="hash-um",
            expires_at=_daqui_a(UM_DIA),
            throttle_seconds=DOIS_MINUTOS,
        )

        stamps = await pendings.stamps_by_token_hash("hash-um")

        assert stamps is not None
        assert stamps.organization_name == "Acme"
        assert stamps.email == LIVRE

"""O diretório de resets em memória. As MESMAS asserções valem para o adaptador
Postgres — a divergência entre os dois tem de aparecer lá, não em produção."""

from datetime import UTC, datetime, timedelta

from sensus_api.password_resets_memory import InMemoryPasswordResetDirectory
from sensus_api.users_memory import InMemoryUserDirectory

UMA_HORA = 3600
DOIS_MINUTOS = 120


def _daqui_a(segundos: int) -> datetime:
    return datetime.now(UTC) + timedelta(seconds=segundos)


async def _com_conta() -> tuple[InMemoryUserDirectory, InMemoryPasswordResetDirectory]:
    users = InMemoryUserDirectory()
    await users.create_organization_with_admin(
        organization_name="Acme", email="dona@exemplo.invalido", password_hash="antiga"
    )
    return users, InMemoryPasswordResetDirectory(users=users)


class TestRequest:
    async def test_devolve_a_conta_quando_o_email_existe(self) -> None:
        users, resets = await _com_conta()

        pedido = await resets.request(
            email="dona@exemplo.invalido",
            token_hash="hash-1",
            expires_at=_daqui_a(UMA_HORA),
            throttle_seconds=DOIS_MINUTOS,
        )

        assert pedido is not None
        assert pedido.email == "dona@exemplo.invalido"
        conta = await users.find_by_email("dona@exemplo.invalido")
        assert conta is not None and pedido.user_id == conta.user_id

    async def test_email_sem_conta_devolve_none(self) -> None:
        _, resets = await _com_conta()

        assert (
            await resets.request(
                email="ninguem@exemplo.invalido",
                token_hash="hash-1",
                expires_at=_daqui_a(UMA_HORA),
                throttle_seconds=DOIS_MINUTOS,
            )
            is None
        )

    async def test_normaliza_o_email_para_minusculas(self) -> None:
        _, resets = await _com_conta()

        assert (
            await resets.request(
                email="DONA@Exemplo.Invalido",
                token_hash="hash-1",
                expires_at=_daqui_a(UMA_HORA),
                throttle_seconds=DOIS_MINUTOS,
            )
            is not None
        )

    async def test_segundo_pedido_dentro_da_janela_devolve_none(self) -> None:
        """Freado e sem conta são indistinguíveis daqui: a borda responde 202 nos
        dois."""
        _, resets = await _com_conta()
        await resets.request(
            email="dona@exemplo.invalido",
            token_hash="hash-1",
            expires_at=_daqui_a(UMA_HORA),
            throttle_seconds=DOIS_MINUTOS,
        )

        assert (
            await resets.request(
                email="dona@exemplo.invalido",
                token_hash="hash-2",
                expires_at=_daqui_a(UMA_HORA),
                throttle_seconds=DOIS_MINUTOS,
            )
            is None
        )

    async def test_pedido_freado_nao_derruba_o_link_anterior(self) -> None:
        """A ordem importa: substituir primeiro e ser freado depois deixaria a pessoa
        sem link nenhum válido."""
        _, resets = await _com_conta()
        await resets.request(
            email="dona@exemplo.invalido",
            token_hash="hash-1",
            expires_at=_daqui_a(UMA_HORA),
            throttle_seconds=DOIS_MINUTOS,
        )

        await resets.request(
            email="dona@exemplo.invalido",
            token_hash="hash-2",
            expires_at=_daqui_a(UMA_HORA),
            throttle_seconds=DOIS_MINUTOS,
        )

        carimbos = await resets.stamps_by_token_hash("hash-1")
        assert carimbos is not None and carimbos.superseded_at is None

    async def test_pedido_fora_da_janela_substitui_o_anterior(self) -> None:
        _, resets = await _com_conta()
        await resets.request(
            email="dona@exemplo.invalido",
            token_hash="hash-1",
            expires_at=_daqui_a(UMA_HORA),
            throttle_seconds=0,
        )

        await resets.request(
            email="dona@exemplo.invalido",
            token_hash="hash-2",
            expires_at=_daqui_a(UMA_HORA),
            throttle_seconds=0,
        )

        antigo = await resets.stamps_by_token_hash("hash-1")
        assert antigo is not None and antigo.superseded_at is not None


class TestConsume:
    async def test_troca_a_senha_incrementa_a_geracao_e_devolve_a_conta(self) -> None:
        users, resets = await _com_conta()
        antes = await users.find_by_email("dona@exemplo.invalido")
        assert antes is not None
        await resets.request(
            email="dona@exemplo.invalido",
            token_hash="hash-1",
            expires_at=_daqui_a(UMA_HORA),
            throttle_seconds=DOIS_MINUTOS,
        )

        conta = await resets.consume(token_hash="hash-1", password_hash="nova")

        assert conta is not None
        assert conta.password_hash == "nova"
        assert conta.session_epoch == antes.session_epoch + 1
        guardada = await users.find_by_email("dona@exemplo.invalido")
        assert guardada is not None and guardada.password_hash == "nova"

    async def test_segundo_uso_do_mesmo_link_devolve_none(self) -> None:
        _, resets = await _com_conta()
        await resets.request(
            email="dona@exemplo.invalido",
            token_hash="hash-1",
            expires_at=_daqui_a(UMA_HORA),
            throttle_seconds=DOIS_MINUTOS,
        )
        await resets.consume(token_hash="hash-1", password_hash="nova")

        assert await resets.consume(token_hash="hash-1", password_hash="outra") is None

    async def test_link_vencido_devolve_none(self) -> None:
        _, resets = await _com_conta()
        await resets.request(
            email="dona@exemplo.invalido",
            token_hash="hash-1",
            expires_at=_daqui_a(-1),
            throttle_seconds=DOIS_MINUTOS,
        )

        assert await resets.consume(token_hash="hash-1", password_hash="nova") is None

    async def test_link_substituido_devolve_none(self) -> None:
        _, resets = await _com_conta()
        await resets.request(
            email="dona@exemplo.invalido",
            token_hash="hash-1",
            expires_at=_daqui_a(UMA_HORA),
            throttle_seconds=0,
        )
        await resets.request(
            email="dona@exemplo.invalido",
            token_hash="hash-2",
            expires_at=_daqui_a(UMA_HORA),
            throttle_seconds=0,
        )

        assert await resets.consume(token_hash="hash-1", password_hash="nova") is None

    async def test_token_inexistente_devolve_none(self) -> None:
        _, resets = await _com_conta()

        assert await resets.consume(token_hash="nao-existe", password_hash="n") is None


class TestStamps:
    async def test_token_inexistente_devolve_none(self) -> None:
        _, resets = await _com_conta()

        assert await resets.stamps_by_token_hash("nao-existe") is None

    async def test_traz_o_email_do_dono(self) -> None:
        """A prévia mostra o e-mail, e quem apresenta o token já o recebeu."""
        _, resets = await _com_conta()
        await resets.request(
            email="dona@exemplo.invalido",
            token_hash="hash-1",
            expires_at=_daqui_a(UMA_HORA),
            throttle_seconds=DOIS_MINUTOS,
        )

        carimbos = await resets.stamps_by_token_hash("hash-1")

        assert carimbos is not None and carimbos.email == "dona@exemplo.invalido"

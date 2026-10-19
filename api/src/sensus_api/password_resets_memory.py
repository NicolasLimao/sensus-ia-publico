"""Diretório de resets em memória — testes de rota e pipeline sem banco."""

import dataclasses
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sensus_api.password_resets import ResetRequest, ResetStamps
from sensus_api.users import Account
from sensus_api.users_memory import InMemoryUserDirectory


@dataclass
class _Reset:
    user_id: str
    email: str
    created_at: datetime
    expires_at: datetime
    used_at: datetime | None = None
    superseded_at: datetime | None = None


@dataclass
class InMemoryPasswordResetDirectory:
    """O par do `PostgresPasswordResetDirectory`.

    Duas propriedades que ele tem por construção e o par real tem por transação — o
    consumo concorrente e o freio concorrente — NÃO são provadas aqui, e é por isso que
    os testes de integração existem.
    """

    users: InMemoryUserDirectory
    resets: dict[str, _Reset] = field(default_factory=dict)

    async def request(
        self,
        *,
        email: str,
        token_hash: str,
        expires_at: datetime,
        throttle_seconds: int,
    ) -> ResetRequest | None:
        account = await self.users.find_by_email(email)
        if account is None:
            return None

        agora = datetime.now(UTC)
        janela = agora - timedelta(seconds=throttle_seconds)
        if any(
            reset.user_id == account.user_id
            and reset.used_at is None
            and reset.superseded_at is None
            and reset.expires_at > agora
            and reset.created_at > janela
            for reset in self.resets.values()
        ):
            return None

        self.resets[token_hash] = _Reset(
            user_id=account.user_id,
            email=account.email,
            created_at=agora,
            expires_at=expires_at,
        )

        # Depois do insert, nunca antes: ver o docstring do port.
        for chave, reset in self.resets.items():
            if (
                chave != token_hash
                and reset.user_id == account.user_id
                and reset.used_at is None
                and reset.superseded_at is None
            ):
                reset.superseded_at = agora

        return ResetRequest(user_id=account.user_id, email=account.email)

    async def stamps_by_token_hash(self, token_hash: str) -> ResetStamps | None:
        reset = self.resets.get(token_hash)
        if reset is None:
            return None
        return ResetStamps(
            email=reset.email,
            expires_at=reset.expires_at,
            used_at=reset.used_at,
            superseded_at=reset.superseded_at,
        )

    async def consume(self, *, token_hash: str, password_hash: str) -> Account | None:
        reset = self.resets.get(token_hash)
        agora = datetime.now(UTC)
        if (
            reset is None
            or reset.used_at is not None
            or reset.superseded_at is not None
            or reset.expires_at <= agora
        ):
            return None

        account = await self.users.find_by_id(reset.user_id)
        if account is None:
            return None

        reset.used_at = agora
        atualizada = dataclasses.replace(
            account,
            password_hash=password_hash,
            session_epoch=account.session_epoch + 1,
        )
        self.users.accounts[account.email] = atualizada
        return atualizada

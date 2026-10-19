"""Diretório de pendências em memória — testes de rota e pipeline sem banco."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sensus_api.pending_signups import SignupRequest, SignupStamps
from sensus_api.users import Account, EmailAlreadyUsed
from sensus_api.users_memory import InMemoryUserDirectory


@dataclass
class _Pending:
    organization_name: str
    email: str
    created_at: datetime
    expires_at: datetime
    consumed_at: datetime | None = None
    superseded_at: datetime | None = None


@dataclass
class InMemoryPendingSignupDirectory:
    """O par do `PostgresPendingSignupDirectory`.

    Duas propriedades que ele tem por construção e o par real tem por transação — o
    freio concorrente e o consumo concorrente — NÃO são provadas aqui, e é por isso que
    os testes de integração existem.
    """

    users: InMemoryUserDirectory
    pendings: dict[str, _Pending] = field(default_factory=dict)

    async def request(
        self,
        *,
        organization_name: str,
        email: str,
        token_hash: str,
        expires_at: datetime,
        throttle_seconds: int,
    ) -> SignupRequest | None:
        chave = email.lower()
        agora = datetime.now(UTC)
        janela = agora - timedelta(seconds=throttle_seconds)
        if any(
            pending.email == chave
            and pending.consumed_at is None
            and pending.superseded_at is None
            and pending.expires_at > agora
            and pending.created_at > janela
            for pending in self.pendings.values()
        ):
            return None

        self.pendings[token_hash] = _Pending(
            organization_name=organization_name,
            email=chave,
            created_at=agora,
            expires_at=expires_at,
        )

        # Depois do insert, nunca antes: ver o docstring do port.
        for outro, pending in self.pendings.items():
            if (
                outro != token_hash
                and pending.email == chave
                and pending.consumed_at is None
                and pending.superseded_at is None
            ):
                pending.superseded_at = agora

        conta = await self.users.find_by_email(chave)
        return SignupRequest(email=chave, already_has_account=conta is not None)

    async def stamps_by_token_hash(self, token_hash: str) -> SignupStamps | None:
        pending = self.pendings.get(token_hash)
        if pending is None:
            return None
        return SignupStamps(
            organization_name=pending.organization_name,
            email=pending.email,
            expires_at=pending.expires_at,
            consumed_at=pending.consumed_at,
            superseded_at=pending.superseded_at,
        )

    async def confirm(self, *, token_hash: str, password_hash: str) -> Account | None:
        pending = self.pendings.get(token_hash)
        agora = datetime.now(UTC)
        if (
            pending is None
            or pending.consumed_at is not None
            or pending.superseded_at is not None
            or pending.expires_at <= agora
        ):
            return None

        pending.consumed_at = agora
        try:
            return await self.users.create_organization_with_admin(
                organization_name=pending.organization_name,
                email=pending.email,
                password_hash=password_hash,
            )
        except EmailAlreadyUsed:
            # No par real isto sai de graça: o `23505` desfaz a transação inteira, e o
            # consumo volta atrás com ela. Aqui o consumo já foi escrito, e desfazê-lo
            # à mão é o que impede o duplo de afirmar um estado que o banco não teria.
            pending.consumed_at = None
            raise

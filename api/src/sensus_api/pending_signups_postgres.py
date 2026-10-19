"""Diretório de pendências no Postgres.

Só `pool` cru, sem `TenantScope` — como o do reset, e por razão mais forte: as três
rotas são anônimas E o tenant ainda não existe quando o pedido é criado. Quem isola é o
token; o que guarda a tabela é ela não ter grant nenhum (migração 009).
"""

from dataclasses import dataclass
from datetime import datetime

from psycopg import errors
from psycopg_pool import AsyncConnectionPool

from sensus_api.pending_signups import SignupRequest, SignupStamps
from sensus_api.users import Account, EmailAlreadyUsed, account_from_row


@dataclass(frozen=True)
class PostgresPendingSignupDirectory:
    pool: AsyncConnectionPool

    async def request(
        self,
        *,
        organization_name: str,
        email: str,
        token_hash: str,
        expires_at: datetime,
        throttle_seconds: int,
    ) -> SignupRequest | None:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                "select * from auth_request_signup(%s, %s, %s, %s, %s)",
                (
                    organization_name,
                    email.lower(),
                    token_hash,
                    expires_at,
                    throttle_seconds,
                ),
            )
            row = await cursor.fetchone()
        if row is None:
            # Freado. A borda responde 202 do mesmo jeito.
            return None
        return SignupRequest(email=row[0], already_has_account=row[1])

    async def stamps_by_token_hash(self, token_hash: str) -> SignupStamps | None:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                "select * from auth_pending_signup_by_token_hash(%s)", (token_hash,)
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return SignupStamps(
            organization_name=row[0],
            email=row[1],
            expires_at=row[2],
            consumed_at=row[3],
            superseded_at=row[4],
        )

    async def confirm(self, *, token_hash: str, password_hash: str) -> Account | None:
        async with self.pool.connection() as connection:
            try:
                cursor = await connection.execute(
                    "select * from auth_confirm_signup(%s, %s)",
                    (token_hash, password_hash),
                )
                row = await cursor.fetchone()
            except errors.UniqueViolation as exc:
                # O endereço ganhou conta entre o pedido e o clique. A transação inteira
                # voltou atrás — inclusive o consumo —, então não há nada a desfazer
                # aqui, só a exceção a traduzir.
                raise EmailAlreadyUsed(token_hash) from exc
        if row is None:
            return None
        return account_from_row(row)

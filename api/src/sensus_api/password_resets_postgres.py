"""Diretório de resets no Postgres.

Só `pool` cru, sem `TenantScope` — diferente do `PostgresInvitationDirectory`, que tem
os dois. Não é esquecimento: as três rotas do reset são anônimas, então não existe
caminho com tenant a proteger por policy. Quem isola é o token, e o que guarda a tabela
é ela não ter grant nenhum (migração 007).
"""

from dataclasses import dataclass
from datetime import datetime

from psycopg_pool import AsyncConnectionPool

from sensus_api.password_resets import ResetRequest, ResetStamps
from sensus_api.users import Account, account_from_row


@dataclass(frozen=True)
class PostgresPasswordResetDirectory:
    pool: AsyncConnectionPool

    async def request(
        self,
        *,
        email: str,
        token_hash: str,
        expires_at: datetime,
        throttle_seconds: int,
    ) -> ResetRequest | None:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                "select * from auth_request_password_reset(%s, %s, %s, %s)",
                (email.lower(), token_hash, expires_at, throttle_seconds),
            )
            row = await cursor.fetchone()
        if row is None:
            # Sem conta ou freado — a função não distingue, e a borda também não.
            return None
        return ResetRequest(user_id=str(row[0]), email=row[1])

    async def stamps_by_token_hash(self, token_hash: str) -> ResetStamps | None:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                "select * from auth_password_reset_by_token_hash(%s)", (token_hash,)
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return ResetStamps(email=row[0], expires_at=row[1], used_at=row[2], superseded_at=row[3])

    async def consume(self, *, token_hash: str, password_hash: str) -> Account | None:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                "select * from auth_consume_password_reset(%s, %s)",
                (token_hash, password_hash),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return account_from_row(row)

"""Diretório de usuários no Postgres.

Toda operação passa por funções `security definer` (migrações 002, 004 e 005) — são a
única forma de tocar `users` sem contexto de tenant, que é exatamente o que o login e
o cadastro precisam. A fresta fica explícita no schema, e nominalmente concedida ao
`sensus_app`, em vez de virar privilégio difuso da aplicação.
"""

from dataclasses import dataclass

from psycopg import errors
from psycopg_pool import AsyncConnectionPool

from sensus_api.users import Account, EmailAlreadyUsed, account_from_row


@dataclass(frozen=True)
class PostgresUserDirectory:
    pool: AsyncConnectionPool

    async def find_by_email(self, email: str) -> Account | None:
        return await self._one("select * from auth_user_by_email(%s)", (email.lower(),))

    async def find_by_id(self, user_id: str) -> Account | None:
        return await self._one("select * from auth_user_by_id(%s)", (user_id,))

    async def create_organization_with_admin(
        self, *, organization_name: str, email: str, password_hash: str
    ) -> Account:
        """O cadastro também passa por uma função `security definer` (migração 004).

        O INSERT em `users` não tem como ter contexto de tenant — o tenant nasce na
        linha anterior — e a policy `users_por_tenant` recusaria a linha. Conectando
        pelo papel de produção, sem bypass, isto era um 500. A função faz os dois
        inserts numa transação só: e-mail repetido derruba a organização junto.
        """
        try:
            account = await self._one(
                "select * from auth_create_organization_with_admin(%s, %s, %s)",
                (organization_name, email.lower(), password_hash),
            )
        except errors.UniqueViolation as exc:
            raise EmailAlreadyUsed(email.lower()) from exc

        assert account is not None  # a função ou devolve a linha ou levanta
        return account

    async def bump_session_epoch(self, user_id: str) -> None:
        """Encerra todas as sessões da pessoa (migração 005).

        Passa pela mesma espécie de fresta `security definer` do login: o UPDATE em
        `users` precisaria de contexto de tenant, e o diretório não tem escopo de
        tenant — nem deveria ganhar um só para isto.
        """
        async with self.pool.connection() as connection:
            await connection.execute("select auth_bump_session_epoch(%s)", (user_id,))

    async def _one(self, sql: str, params: tuple[str, ...]) -> Account | None:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(sql, params)
            row = await cursor.fetchone()
        if row is None:
            return None
        return account_from_row(row)

"""Diretório de convites no Postgres.

Duas dependências, e a diferença entre elas é a decisão central desta fatia:

- `scope` (`TenantScope`) para **criar, listar e revogar**. O admin tem tenant — é
  exatamente a situação para a qual a policy da 006 existe, e é ela que pega um `where`
  meu escrito errado.
- `pool` cru para as **duas frestas** `security definer` (prévia e aceite). O convidado
  não tem tenant nenhum a informar; é a mesma justificativa das frestas do login.

Consequência deliberada: `list_pending` e `revoke` **não** carregam `where tenant_id =
%s`. Quem isola é a policy. Diferente do `PostgresChunkRepository`, que tem as duas
camadas — e diferente de propósito: um `where` meu por cima esconderia uma policy
quebrada, e o teste de leitura cruzada ficaria passando por engano.
"""

from dataclasses import dataclass
from datetime import datetime

from psycopg import errors
from psycopg_pool import AsyncConnectionPool

from sensus_api.invitations import Invitation, InvitationStamps
from sensus_api.users import Account, EmailAlreadyUsed, account_from_row
from sensus_rag.tenant_scope import TenantScope


@dataclass(frozen=True)
class PostgresInvitationDirectory:
    scope: TenantScope
    pool: AsyncConnectionPool

    async def create(
        self,
        *,
        tenant_id: str,
        created_by: str,
        email: str,
        token_hash: str,
        expires_at: datetime,
    ) -> Invitation:
        async with self.scope.connection(tenant_id) as connection:
            # Substitui o pendente anterior do MESMO endereço. Sem isto, "reenviar"
            # deixa dois links vivos e a pessoa clica no que já não vale.
            #
            # Sem `where tenant_id`: quem o acrescenta é a policy da 006 (ver o docstring
            # do módulo). O `email` já basta para escolher a linha dentro do tenant.
            await connection.execute(
                "update invitations set revoked_at = now() "
                "where email = %s and accepted_at is null and revoked_at is null",
                (email.lower(),),
            )
            cursor = await connection.execute(
                "insert into invitations "
                "(tenant_id, token_hash, created_by, expires_at, email) "
                "values (%s, %s, %s, %s, %s) returning id, created_at, expires_at, email",
                (tenant_id, token_hash, created_by, expires_at, email.lower()),
            )
            row = await cursor.fetchone()
        assert row is not None  # `insert ... returning` ou devolve linha ou levanta
        return Invitation(id=str(row[0]), created_at=row[1], expires_at=row[2], email=row[3])

    async def list_pending(self, *, tenant_id: str) -> list[Invitation]:
        """Só os pendentes, e o estado é derivado dos carimbos — não há coluna `status`.

        Sem `where tenant_id`: quem o acrescenta é a policy (ver o docstring do módulo).
        """
        async with self.scope.connection(tenant_id) as connection:
            cursor = await connection.execute(
                "select id, created_at, expires_at, email from invitations "
                "where accepted_at is null and revoked_at is null and expires_at > now() "
                "order by created_at desc"
            )
            rows = await cursor.fetchall()
        return [
            Invitation(id=str(row[0]), created_at=row[1], expires_at=row[2], email=row[3])
            for row in rows
        ]

    async def revoke(self, *, tenant_id: str, invitation_id: str) -> None:
        """Idempotente: zero linhas afetadas não é erro.

        O `and accepted_at is null` é o que faz a revogação morder só o pendente —
        revogar quem já entrou não desfaz nada, e fingir que desfaz seria pior.
        """
        async with self.scope.connection(tenant_id) as connection:
            await connection.execute(
                "update invitations set revoked_at = now() "
                "where id = %s and accepted_at is null and revoked_at is null",
                (invitation_id,),
            )

    async def stamps_by_token_hash(self, token_hash: str) -> InvitationStamps | None:
        """Pela fresta: quem chega aqui não tem tenant, e a policy devolveria zero
        linhas para qualquer convite."""
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                "select * from auth_invitation_by_token_hash(%s)", (token_hash,)
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return InvitationStamps(
            organization_name=row[0],
            email=row[1],
            expires_at=row[2],
            accepted_at=row[3],
            revoked_at=row[4],
        )

    async def accept(self, *, token_hash: str, password_hash: str) -> Account | None:
        """Consome o convite e cria o `member` com o e-mail DO CONVITE, numa transação
        só (migração 008).

        `None` quando não havia o que consumir — usado, vencido, revogado ou
        inexistente, sem distinguir. Quem distingue é a borda.
        """
        try:
            async with self.pool.connection() as connection:
                cursor = await connection.execute(
                    "select * from auth_accept_invitation(%s, %s)",
                    (token_hash, password_hash),
                )
                row = await cursor.fetchone()
        except errors.UniqueViolation as exc:
            # O 23505 do `insert` em `users`. A função inteira voltou atrás: o convite
            # continua pendente. O e-mail vem do convite, então quem repete a tentativa
            # obtém o mesmo resultado — mas o link não queima, que é o que importa.
            raise EmailAlreadyUsed(token_hash) from exc
        if row is None:
            return None
        return account_from_row(row)

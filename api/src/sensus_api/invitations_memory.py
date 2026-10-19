"""Diretório de convites em memória — pipeline sem banco e testes de rota."""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sensus_api.invitations import Invitation, InvitationStamps
from sensus_api.users import Account
from sensus_api.users_memory import InMemoryUserDirectory


@dataclass
class _Row:
    """A linha como a tabela a guarda. Mutável: os carimbos são escritos no lugar."""

    id: str
    tenant_id: str
    token_hash: str
    email: str
    created_by: str
    created_at: datetime
    expires_at: datetime
    accepted_at: datetime | None = None
    accepted_user_id: str | None = None
    revoked_at: datetime | None = None


@dataclass
class InMemoryInvitationDirectory:
    # O duplo de usuários pelo tipo CONCRETO, e não pelo Protocol: aceitar um convite
    # cria usuário, e `create_member` existe só no duplo (ver `users_memory.py`).
    users: InMemoryUserDirectory
    rows: list[_Row] = field(default_factory=list)

    async def create(
        self,
        *,
        tenant_id: str,
        created_by: str,
        email: str,
        token_hash: str,
        expires_at: datetime,
    ) -> Invitation:
        # Substituir vem ANTES do insert, ao contrário do reset de senha: lá o insert
        # podia ser abortado pelo freio, e revogar antes deixaria a pessoa sem link
        # nenhum. Aqui não há freio que aborte — se algo falhar, nada foi criado.
        for existente in self.rows:
            if (
                existente.tenant_id == tenant_id
                and existente.email == email.lower()
                and self._pending(existente, datetime.now(UTC))
            ):
                existente.revoked_at = datetime.now(UTC)
        row = _Row(
            # `str(uuid4())` e não `.hex`: o par real devolve o uuid do Postgres com
            # hífens, e um id de forma diferente aqui esconderia, no duplo, qualquer
            # bug de quem trata esse id como uuid.
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            token_hash=token_hash,
            email=email.lower(),
            created_by=created_by,
            created_at=datetime.now(UTC),
            expires_at=expires_at,
        )
        self.rows.append(row)
        return Invitation(
            id=row.id, created_at=row.created_at, expires_at=row.expires_at, email=row.email
        )

    async def list_pending(self, *, tenant_id: str) -> list[Invitation]:
        agora = datetime.now(UTC)
        # Mais recente primeiro, como o `order by created_at desc` do par real. O
        # desempate pelo índice de inserção não é zelo: `datetime.now` tem resolução de
        # milissegundos, dois convites criados na mesma requisição de teste podem
        # empatar no carimbo, e `sorted` é estável — sem o desempate, o empate sairia na
        # ordem de INSERÇÃO, isto é, ao contrário do pedido.
        ordenadas = sorted(
            enumerate(self.rows), key=lambda par: (par[1].created_at, par[0]), reverse=True
        )
        return [
            Invitation(
                id=row.id,
                created_at=row.created_at,
                expires_at=row.expires_at,
                email=row.email,
            )
            for _, row in ordenadas
            if row.tenant_id == tenant_id and self._pending(row, agora)
        ]

    async def revoke(self, *, tenant_id: str, invitation_id: str) -> None:
        agora = datetime.now(UTC)
        for row in self.rows:
            if row.id == invitation_id and row.tenant_id == tenant_id and self._pending(row, agora):
                row.revoked_at = agora
                return

    async def stamps_by_token_hash(self, token_hash: str) -> InvitationStamps | None:
        for row in self.rows:
            if row.token_hash == token_hash:
                return InvitationStamps(
                    organization_name=self.users.organization_names.get(row.tenant_id, ""),
                    email=row.email,
                    expires_at=row.expires_at,
                    accepted_at=row.accepted_at,
                    revoked_at=row.revoked_at,
                )
        return None

    async def accept(self, *, token_hash: str, password_hash: str) -> Account | None:
        agora = datetime.now(UTC)
        for row in self.rows:
            if row.token_hash == token_hash and self._pending(row, agora):
                # A ORDEM aqui é a única forma de o duplo empatar com o par real. Lá, o
                # `insert` que falha por e-mail repetido derruba a transação e o
                # `accepted_at` volta atrás. Aqui, consumir ANTES de tentar criar
                # deixaria o convite queimado por uma tentativa que falhou —
                # divergência silenciosa entre duplo e real, a categoria de defeito que
                # mais escapou à revisão neste projeto. Criar primeiro, carimbar depois.
                account = await self.users.create_member(
                    tenant_id=row.tenant_id, email=row.email, password_hash=password_hash
                )
                row.accepted_at = agora
                row.accepted_user_id = account.user_id
                return account
        return None

    @staticmethod
    def _pending(row: _Row, agora: datetime) -> bool:
        """O estado é derivado dos carimbos, aqui como no SQL — não há coluna `status`."""
        return row.accepted_at is None and row.revoked_at is None and row.expires_at > agora

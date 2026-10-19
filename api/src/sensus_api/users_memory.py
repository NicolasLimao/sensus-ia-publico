"""Diretório de usuários em memória — pipeline sem banco e testes de rota."""

import dataclasses
import uuid
from dataclasses import dataclass, field

from sensus_api.users import Account, EmailAlreadyUsed


@dataclass
class InMemoryUserDirectory:
    accounts: dict[str, Account] = field(default_factory=dict)
    # tenant_id -> nome. No par real isto é a tabela `organizations`, lida pela prévia
    # do convite num `join`; sem guardá-lo aqui, o duplo não teria o que mostrar.
    organization_names: dict[str, str] = field(default_factory=dict)

    async def find_by_email(self, email: str) -> Account | None:
        return self.accounts.get(email.lower())

    async def find_by_id(self, user_id: str) -> Account | None:
        for account in self.accounts.values():
            if account.user_id == user_id:
                return account
        return None

    async def create_organization_with_admin(
        self, *, organization_name: str, email: str, password_hash: str
    ) -> Account:
        key = email.lower()
        if key in self.accounts:
            raise EmailAlreadyUsed(key)
        account = Account(
            # `str(uuid4())` e não `.hex`: o par real devolve o uuid do Postgres com
            # hífens, e um id de forma diferente aqui esconderia, no duplo, qualquer
            # bug de quem trata esse id como uuid.
            user_id=str(uuid.uuid4()),
            tenant_id=str(uuid.uuid4()),
            email=key,
            password_hash=password_hash,
            role="admin",
            session_epoch=0,
        )
        self.accounts[key] = account
        self.organization_names[account.tenant_id] = organization_name
        return account

    async def create_member(self, *, tenant_id: str, email: str, password_hash: str) -> Account:
        """Entra numa organização que já existe, sempre como `member`.

        Só o duplo tem isto, e de propósito: no par real o `insert` do membro acontece
        DENTRO de `auth_accept_invitation` (migração 006), numa transação que também
        consome o convite. Pôr o método no `UserDirectory` obrigaria o adaptador
        Postgres a implementar o que ele nunca chamaria.
        """
        key = email.lower()
        if key in self.accounts:
            raise EmailAlreadyUsed(key)
        account = Account(
            user_id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            email=key,
            password_hash=password_hash,
            role="member",
            session_epoch=0,
        )
        self.accounts[key] = account
        return account

    async def bump_session_epoch(self, user_id: str) -> None:
        for key, account in self.accounts.items():
            if account.user_id == user_id:
                self.accounts[key] = dataclasses.replace(
                    account, session_epoch=account.session_epoch + 1
                )
                return

"""Contrato do diretório de usuários.

`sensus_api` conhece usuário e papel; `sensus_rag` não — ele é o RAG genérico. Esta
fronteira é deliberada e está na spec (§5.3).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol


class EmailAlreadyUsed(Exception):
    """E-mail já tem conta. Vira 409 na borda."""


@dataclass(frozen=True)
class Account:
    user_id: str
    tenant_id: str
    email: str
    password_hash: str
    role: str
    session_epoch: int


def account_from_row(row: Sequence[Any]) -> Account:
    """Uma linha das funções SQL que devolvem conta, virada em `Account`.

    As cinco (`auth_user_by_email`, `auth_user_by_id`,
    `auth_create_organization_with_admin`, `auth_accept_invitation` e
    `auth_consume_password_reset`) combinaram a MESMA ordem de colunas, e é essa
    combinação que este mapeamento guarda. Copiado em cada adaptador, seriam três
    lugares onde a ordem pode divergir em silêncio — nenhum tipo pega índice posicional
    trocado.
    """
    return Account(
        user_id=str(row[0]),
        tenant_id=str(row[1]),
        email=row[2],
        password_hash=row[3],
        role=row[4],
        session_epoch=row[5],
    )


class UserDirectory(Protocol):
    async def find_by_email(self, email: str) -> Account | None: ...

    async def find_by_id(self, user_id: str) -> Account | None: ...

    async def create_organization_with_admin(
        self, *, organization_name: str, email: str, password_hash: str
    ) -> Account: ...

    async def bump_session_epoch(self, user_id: str) -> None:
        """Encerra TODAS as sessões da pessoa: os tokens já emitidos carregam o valor
        anterior e passam a divergir do que está gravado.

        Devolve `None` de propósito, embora a função SQL devolva o novo contador —
        quem chama é o logout, que vai apagar o cookie, e devolver o valor só criaria
        a tentação de reemitir sessão a partir dele.
        """
        ...

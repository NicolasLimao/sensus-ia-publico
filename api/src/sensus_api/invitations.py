"""Contrato do diretório de convites.

Um convite é o caminho de entrada para uma organização que JÁ existe — o cadastro
(`users.py`) cria uma nova. Quem aceita entra sempre como `member`.

O port nunca vê o token em claro, só o `sha256` dele: quem sorteia e resume é a borda
(`routes/invites.py`). Um diretório que recebesse o token teria de conhecer o algoritmo
do resumo, e aí haveria dois lugares para errá-lo.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sensus_api.users import Account


@dataclass(frozen=True)
class Invitation:
    """Um convite como o admin o vê. Sem o token: desde a 3e ele não existe fora do
    e-mail — nem na resposta da criação."""

    id: str
    created_at: datetime
    expires_at: datetime
    email: str


@dataclass(frozen=True)
class InvitationStamps:
    """Os carimbos crus, como o banco os guarda.

    Quem deriva o veredito — pendente, usado, vencido ou revogado — é a borda. Uma
    consulta que devolvesse só "pendente ou não" deixaria a borda sem como distinguir
    "já usado" de "vencido" de "inexistente", e é justamente essa distinção que a tela
    de quem foi convidado precisa mostrar.
    """

    organization_name: str
    # Desde a 3e. Não revela nada novo: quem apresenta o token o recebeu naquela caixa —
    # e é o que permite à tela dizer "você foi convidado como fulano@x" em vez de pedir fé.
    email: str
    expires_at: datetime
    accepted_at: datetime | None
    revoked_at: datetime | None


class InvitationDirectory(Protocol):
    async def create(
        self,
        *,
        tenant_id: str,
        created_by: str,
        email: str,
        token_hash: str,
        expires_at: datetime,
    ) -> Invitation:
        """`expires_at` chega pronto: quem conhece o prazo é quem tem as `settings`."""
        ...

    async def list_pending(self, *, tenant_id: str) -> list[Invitation]: ...

    async def revoke(self, *, tenant_id: str, invitation_id: str) -> None:
        """Idempotente: revogar o que não existe, o que já foi aceito ou o que já foi
        revogado não é erro — a borda responde 204 em todos os casos."""
        ...

    async def stamps_by_token_hash(self, token_hash: str) -> InvitationStamps | None: ...

    async def accept(self, *, token_hash: str, password_hash: str) -> Account | None:
        """Consome o convite e cria o `member` com o e-mail DO CONVITE, ou devolve
        `None` se não havia o que consumir — sem dizer por quê. Quem distingue o motivo
        é a borda, chamando `stamps_by_token_hash` só no caminho que já falhou.

        O e-mail saiu da assinatura na 3e: ele vem da linha, e não de quem chama. Não é
        validação a mais — é a ausência de uma entrada por onde outro endereço entraria.

        Levanta `EmailAlreadyUsed` se o e-mail já tem conta (em outra organização; na
        própria, a borda barra antes), e nesse caso o convite **continua pendente**: no
        par real o `insert` que falha derruba a transação inteira, e o consumo volta
        atrás.
        """
        ...

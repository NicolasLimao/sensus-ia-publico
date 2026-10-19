"""Contrato do diretório de resets de senha.

Como no convite, o port nunca vê o token em claro, só o `sha256`: quem sorteia e resume
é a borda (`routes/password_reset.py`). Um diretório que recebesse o token teria de
conhecer o algoritmo do resumo, e aí haveria dois lugares para errá-lo.

Nenhum método leva `tenant_id`: as três rotas desta fatia são anônimas, e quem chega com
o link não tem tenant a informar.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sensus_api.users import Account


@dataclass(frozen=True)
class ResetRequest:
    """O que a borda precisa saber para mandar o e-mail: para quem, e de quem é a
    conta. O e-mail vem do BANCO, em minúsculas — e não o que o chamador digitou, que
    pode diferir na caixa."""

    user_id: str
    email: str


@dataclass(frozen=True)
class ResetStamps:
    """Os carimbos crus, como o banco os guarda.

    Quem deriva o veredito — pendente, usado, substituído ou vencido — é a borda. Uma
    consulta que devolvesse só "vale ou não" deixaria a tela sem como dizer o que fazer
    em seguida, que é a diferença entre "faça login" e "abra o e-mail mais recente".
    """

    email: str
    expires_at: datetime
    used_at: datetime | None
    superseded_at: datetime | None


class PasswordResetDirectory(Protocol):
    async def request(
        self,
        *,
        email: str,
        token_hash: str,
        expires_at: datetime,
        throttle_seconds: int,
    ) -> ResetRequest | None:
        """Cria o pedido, ou devolve `None` **sem dizer por quê**.

        `None` cobre dois casos que a borda não pode distinguir: não há conta com
        aquele e-mail, ou já houve pedido dentro da janela. Os dois respondem 202.

        Substituir os pendentes anteriores acontece DEPOIS de o novo existir: o
        contrário deixaria a pessoa sem link nenhum válido no caso freado.
        """
        ...

    async def stamps_by_token_hash(self, token_hash: str) -> ResetStamps | None: ...

    async def consume(self, *, token_hash: str, password_hash: str) -> Account | None:
        """Consome o link, grava a senha nova e incrementa `session_epoch` — os três
        num átomo só. `None` quando não havia o que consumir, sem dizer por quê: quem
        distingue é a borda, chamando `stamps_by_token_hash` só no caminho que já
        falhou.

        A conta devolvida já traz a geração NOVA, e é com ela que a borda emite o
        cookie — emitir com a geração antiga produziria uma sessão que o próprio
        incremento acabou de invalidar.
        """
        ...

"""Contrato do diretório de pendências de cadastro.

Como no convite e no reset, o port nunca vê o token em claro, só o `sha256`: quem
sorteia e resume é a borda (`routes/signup_confirmation.py`). Um diretório que recebesse
o token teria de conhecer o algoritmo do resumo, e aí haveria dois lugares para errá-lo.

Nenhum método leva `tenant_id`, e aqui a razão é mais forte que no reset: as três rotas
são anônimas E o tenant ainda não existe — é a confirmação que o cria.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sensus_api.users import Account


@dataclass(frozen=True)
class SignupRequest:
    """O que a borda precisa saber para escolher QUAL e-mail mandar.

    `already_has_account` nunca chega ao navegador: a resposta HTTP é a mesma dos dois
    lados, e é a caixa de entrada que distingue. O e-mail devolvido é o normalizado,
    não o que o chamador digitou.
    """

    email: str
    already_has_account: bool


@dataclass(frozen=True)
class SignupStamps:
    """Os carimbos crus, como o banco os guarda.

    Quem deriva o veredito — pendente, usado, substituído ou vencido — é a borda. Uma
    consulta que devolvesse só "vale ou não" deixaria a tela sem como dizer o que fazer
    em seguida, que é a diferença entre "faça login" e "abra o e-mail mais recente".
    """

    organization_name: str
    email: str
    expires_at: datetime
    consumed_at: datetime | None
    superseded_at: datetime | None


class PendingSignupDirectory(Protocol):
    async def request(
        self,
        *,
        organization_name: str,
        email: str,
        token_hash: str,
        expires_at: datetime,
        throttle_seconds: int,
    ) -> SignupRequest | None:
        """Cria a pendência, ou devolve `None` quando freada — e a borda responde 202
        do mesmo jeito.

        A pendência é criada MESMO quando o endereço já tem conta: sem linha não há
        sobre o que aplicar o freio, e a rota anônima viraria amplificador de e-mail
        contra um endereço conhecido.

        Substituir as pendências anteriores acontece DEPOIS de a nova existir: o
        contrário deixaria a pessoa sem link nenhum válido no caso freado.
        """
        ...

    async def stamps_by_token_hash(self, token_hash: str) -> SignupStamps | None: ...

    async def confirm(self, *, token_hash: str, password_hash: str) -> Account | None:
        """Consome a pendência e cria organização + admin num átomo só.

        `None` quando não havia o que consumir, sem dizer por quê: quem distingue é a
        borda, chamando `stamps_by_token_hash` só no caminho que já falhou.

        Levanta `EmailAlreadyUsed` quando o endereço ganhou conta entre o pedido e o
        clique. Nesse caso NADA fica feito — nem o consumo —, porque a transação volta
        atrás inteira.
        """
        ...

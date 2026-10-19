"""Contrato de envio de e-mail.

Porta do PRODUTO, não do reset de senha: o formato não menciona senha nem link, porque
o próximo consumidor (convite por e-mail, fatia 3e) manda outra coisa pelo mesmo cano.

Só `text/plain`. HTML é fatia posterior — e um e-mail de uma frase com um link não
melhora em HTML, só ganha mais jeito de cair em filtro de spam.
"""

import logging
from dataclasses import dataclass
from typing import Protocol

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EmailMessage:
    to: str
    subject: str
    body: str


class EmailSender(Protocol):
    async def send(self, message: EmailMessage) -> None:
        """Levanta em caso de falha de envio. Quem chama decide o que fazer — e no
        reset a resposta ao navegador já saiu, então só o log sobra."""
        ...


@dataclass(frozen=True)
class LoggingEmailSender:
    """O provedor de quem não configurou SMTP: escreve o e-mail no log.

    É o que faz o fluxo inteiro rodar numa máquina de desenvolvimento — inclusive o
    link, que o desenvolvedor copia do terminal. Em produção, cair aqui por engano
    significa nenhum e-mail entregue e o link no log do servidor; por isso `app.py` só
    o escolhe quando `smtp_host` está vazio, e avisa no log ao subir.
    """

    async def send(self, message: EmailMessage) -> None:
        logger.info(
            "e-mail NÃO enviado (sem SMTP configurado) — para=%s assunto=%s\n%s",
            message.to,
            message.subject,
            message.body,
        )

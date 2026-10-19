"""Envio por SMTP — o provedor real.

SMTP genérico, e não a API de um fornecedor, porque serve Resend, SES, Postmark,
Mailgun ou o servidor do próprio cliente sem trocar código: num produto multi-tenant o
cliente pode querer que o e-mail saia do domínio dele. O preço é menos sinal de entrega
(sem id de mensagem, sem webhook de bounce); quando isso doer, o que muda é este
arquivo, e só ele.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from email.message import EmailMessage as MimeMessage
from typing import Any

import aiosmtplib

from sensus_api.email import EmailMessage

# Injetável para o teste verificar o que vai ao servidor sem haver servidor. A
# assinatura é a do `aiosmtplib.send`.
SendCallable = Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class SmtpEmailSender:
    host: str
    port: int
    username: str
    password: str
    starttls: bool
    sender: str
    # `send_callable`, e não `send`, para não colidir com o método abaixo.
    send_callable: SendCallable = field(default=aiosmtplib.send)

    async def send(self, message: EmailMessage) -> None:
        mime = MimeMessage()
        mime["From"] = self.sender
        mime["To"] = message.to
        mime["Subject"] = message.subject
        mime.set_content(message.body)

        parametros: dict[str, Any] = {
            "hostname": self.host,
            "port": self.port,
            "start_tls": self.starttls,
        }
        # Relay interno sem autenticação existe; mandar credencial vazia faz o servidor
        # recusar o AUTH em vez de aceitar a conexão anônima.
        if self.username:
            parametros["username"] = self.username
            parametros["password"] = self.password

        await self.send_callable(mime, **parametros)

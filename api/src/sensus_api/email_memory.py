"""Provedor de e-mail em memória — testes de rota e pipeline sem SMTP."""

from dataclasses import dataclass, field

from sensus_api.email import EmailMessage


@dataclass
class RecordingEmailSender:
    sent: list[EmailMessage] = field(default_factory=list)

    async def send(self, message: EmailMessage) -> None:
        self.sent.append(message)

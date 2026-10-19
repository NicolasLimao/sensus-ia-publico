"""O adaptador SMTP, sem servidor nenhum.

O que este adaptador decide — cabeçalhos da mensagem e parâmetros da conexão — é
verificável sem rede. Que o servidor real aceite o que ele monta é outra pergunta, e
quem responde é `TestContratoSmtp`, no fim do arquivo.
"""

from dataclasses import dataclass, field
from email.message import EmailMessage as MimeMessage
from typing import Any

import pytest

from sensus_api.config import get_settings
from sensus_api.email import EmailMessage
from sensus_api.email_smtp import SmtpEmailSender


@dataclass
class TransporteFalso:
    chamadas: list[tuple[MimeMessage, dict[str, Any]]] = field(default_factory=list)

    async def __call__(self, mime: MimeMessage, **kwargs: Any) -> None:
        self.chamadas.append((mime, kwargs))


def _sender(transporte: TransporteFalso) -> SmtpEmailSender:
    return SmtpEmailSender(
        host="smtp.exemplo.invalido",
        port=587,
        username="usuario",
        password="segredo",
        starttls=True,
        sender="Sensus <nao-responda@exemplo.invalido>",
        send_callable=transporte,
    )


class TestSmtpEmailSender:
    async def test_monta_a_mensagem_com_remetente_destinatario_e_assunto(self) -> None:
        transporte = TransporteFalso()

        await _sender(transporte).send(
            EmailMessage(to="alguem@exemplo.invalido", subject="Assunto", body="Corpo")
        )

        mime, _ = transporte.chamadas[0]
        assert mime["To"] == "alguem@exemplo.invalido"
        assert mime["From"] == "Sensus <nao-responda@exemplo.invalido>"
        assert mime["Subject"] == "Assunto"
        assert mime.get_content().strip() == "Corpo"

    async def test_passa_host_porta_credenciais_e_starttls_ao_transporte(self) -> None:
        transporte = TransporteFalso()

        await _sender(transporte).send(EmailMessage(to="a@b.invalido", subject="s", body="c"))

        _, kwargs = transporte.chamadas[0]
        assert kwargs["hostname"] == "smtp.exemplo.invalido"
        assert kwargs["port"] == 587
        assert kwargs["username"] == "usuario"
        assert kwargs["password"] == "segredo"
        assert kwargs["start_tls"] is True

    async def test_sem_usuario_nao_manda_credencial(self) -> None:
        """Relay interno sem autenticação existe, e mandar `username=""` faria o
        servidor recusar o AUTH em vez de aceitar a conexão anônima."""
        transporte = TransporteFalso()
        sender = SmtpEmailSender(
            host="relay.interno.invalido",
            port=25,
            username="",
            password="",
            starttls=False,
            sender="nao-responda@exemplo.invalido",
            send_callable=transporte,
        )

        await sender.send(EmailMessage(to="a@b.invalido", subject="s", body="c"))

        _, kwargs = transporte.chamadas[0]
        assert "username" not in kwargs
        assert "password" not in kwargs


@pytest.mark.contract
class TestContratoSmtp:
    """Manda um e-mail de verdade pelo SMTP configurado.

    Fora da suíte padrão (`addopts` filtra `contract`). Lê das MESMAS settings que a
    aplicação usa — nunca de `os.environ`, o furo que a 2b fechou: as chaves vivem no
    `api/.env`, lido por pydantic-settings e não exportado para o processo, então um
    teste que lesse o ambiente PULARIA numa máquina configurada, que é exatamente a
    divergência silenciosa que ele existe para pegar.
    """

    async def test_entrega_ao_servidor_real(self) -> None:
        settings = get_settings()
        if not settings.smtp_host:
            pytest.skip("SENSUS_SMTP_HOST não configurado")

        sender = SmtpEmailSender(
            host=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_username,
            password=settings.smtp_password,
            starttls=settings.smtp_starttls,
            sender=settings.email_from,
        )

        await sender.send(
            EmailMessage(
                to=settings.email_from,
                subject="Sensus — teste de contrato SMTP",
                body="Se este e-mail chegou, o adaptador SMTP fala com o servidor real.",
            )
        )

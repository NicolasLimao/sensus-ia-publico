"""A porta de e-mail e as duas implementações que não falam com servidor nenhum."""

import logging

import pytest

from sensus_api.email import EmailMessage, LoggingEmailSender
from sensus_api.email_memory import RecordingEmailSender

MENSAGEM = EmailMessage(
    to="alguem@exemplo.invalido",
    subject="Redefinir sua senha",
    body="Abra este link: https://sensus.invalido/redefinir#abc",
)


class TestRecordingEmailSender:
    async def test_guarda_as_mensagens_na_ordem(self) -> None:
        sender = RecordingEmailSender()

        await sender.send(MENSAGEM)

        assert sender.sent == [MENSAGEM]


class TestLoggingEmailSender:
    async def test_escreve_o_corpo_inteiro_no_log(self, caplog: pytest.LogCaptureFixture) -> None:
        """O link vai no log de propósito: é ele que faz o fluxo rodar numa máquina de
        desenvolvimento sem SMTP. Um log que escondesse o corpo deixaria o
        desenvolvedor sem como abrir o próprio link."""
        sender = LoggingEmailSender()

        with caplog.at_level(logging.INFO, logger="sensus_api.email"):
            await sender.send(MENSAGEM)

        registro = caplog.text
        assert "alguem@exemplo.invalido" in registro
        assert "https://sensus.invalido/redefinir#abc" in registro

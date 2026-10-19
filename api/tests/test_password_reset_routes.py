"""As três rotas do reset, contra os duplos.

O que NÃO está aqui, e por quê: consumo concorrente e freio concorrente são
propriedades da transação, e o duplo os acerta por construção sem provar nada. Ficam
nos testes de integração, contra o Postgres.
"""

import re
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from conftest import cadastrar, settings_sem_env, tls_client
from sensus_api.app import create_app
from sensus_api.email_memory import RecordingEmailSender
from sensus_api.password_resets import ResetStamps
from sensus_api.routes.password_reset import verdict_for
from sensus_api.users_memory import InMemoryUserDirectory

CONTA = "dona@exemplo.invalido"
SENHA = "senha-bem-longa"
NOVA = "outra-senha-bem-longa"


def _app_com_conta() -> tuple[TestClient, RecordingEmailSender, InMemoryUserDirectory]:
    """Aplicação com uma conta cadastrada e o navegador SEM sessão — a situação de quem
    esqueceu a senha.

    Monta a própria aplicação, em vez de usar a fixture `app`, porque precisa de
    `public_base_url` (o link do e-mail sai dela) e do provedor que grava as mensagens.
    `tls_client` porque o cookie de sessão é `Secure`: sob `http://` o cliente o
    descartaria e o teste de "já entra logado" falharia por motivo errado.
    """
    email = RecordingEmailSender()
    app = create_app(
        settings_sem_env(
            session_secret="segredo-de-teste",
            public_base_url="https://sensus.invalido",
        ),
        email=email,
    )
    client = tls_client(app)
    cadastrar(client, app, organization_name="Acme", email=CONTA, password=SENHA)
    # O cadastro também manda e-mail desde a 3f, e aqui ele é ANDAIME, não o assunto:
    # limpar a caixa mantém os testes deste arquivo falando só das mensagens do reset,
    # inclusive os que CONTAM quantas saíram.
    email.sent.clear()
    client.cookies.clear()
    return client, email, app.state.users


def _token_do_email(email: RecordingEmailSender) -> str:
    achado = re.search(r"#([A-Za-z0-9_-]+)", email.sent[-1].body)
    assert achado is not None, f"nenhum token no corpo: {email.sent[-1].body}"
    return achado.group(1)


class TestForgotPassword:
    def test_conta_existente_responde_202_e_manda_o_email(self) -> None:
        client, email, _ = _app_com_conta()

        resposta = client.post("/api/v1/auth/forgot-password", json={"email": CONTA})

        assert resposta.status_code == 202
        assert len(email.sent) == 1
        assert email.sent[0].to == CONTA

    def test_conta_inexistente_responde_o_MESMO_202_e_nao_manda_nada(self) -> None:
        """O corpo e o status são idênticos; o tempo também, porque o envio sai depois
        da resposta. Distinguir aqui entregaria quais e-mails têm conta."""
        client, email, _ = _app_com_conta()
        com_conta = client.post("/api/v1/auth/forgot-password", json={"email": CONTA})

        sem_conta = client.post(
            "/api/v1/auth/forgot-password", json={"email": "ninguem@exemplo.invalido"}
        )

        assert sem_conta.status_code == com_conta.status_code == 202
        assert sem_conta.json() == com_conta.json()
        assert len(email.sent) == 1

    def test_pedido_freado_responde_o_MESMO_202(self) -> None:
        client, email, _ = _app_com_conta()
        client.post("/api/v1/auth/forgot-password", json={"email": CONTA})

        resposta = client.post("/api/v1/auth/forgot-password", json={"email": CONTA})

        assert resposta.status_code == 202
        assert len(email.sent) == 1

    def test_o_link_aponta_para_a_base_publica_e_leva_o_token_no_fragmento(self) -> None:
        client, email, _ = _app_com_conta()

        client.post("/api/v1/auth/forgot-password", json={"email": CONTA})

        assert "https://sensus.invalido/redefinir#" in email.sent[0].body

    def test_email_invalido_e_422(self) -> None:
        client, _, _ = _app_com_conta()

        assert (
            client.post("/api/v1/auth/forgot-password", json={"email": "nao-e-email"}).status_code
            == 422
        )


class TestPreview:
    def test_link_valido_devolve_o_email_do_dono(self) -> None:
        client, email, _ = _app_com_conta()
        client.post("/api/v1/auth/forgot-password", json={"email": CONTA})

        resposta = client.post(
            "/api/v1/auth/reset-password/preview",
            json={"token": _token_do_email(email)},
        )

        assert resposta.status_code == 200
        assert resposta.json()["email"] == CONTA

    def test_token_inexistente_e_404(self) -> None:
        client, _, _ = _app_com_conta()

        resposta = client.post("/api/v1/auth/reset-password/preview", json={"token": "nao-existe"})

        assert resposta.status_code == 404
        assert resposta.json()["detail"]["code"] == "reset_not_found"

    def test_link_ja_usado_e_410_com_o_motivo(self) -> None:
        client, email, _ = _app_com_conta()
        client.post("/api/v1/auth/forgot-password", json={"email": CONTA})
        token = _token_do_email(email)
        client.post("/api/v1/auth/reset-password", json={"token": token, "password": NOVA})

        resposta = client.post("/api/v1/auth/reset-password/preview", json={"token": token})

        assert resposta.status_code == 410
        assert resposta.json()["detail"]["code"] == "reset_used"


class TestResetPassword:
    def test_troca_a_senha_e_ja_emite_sessao(self) -> None:
        client, email, _ = _app_com_conta()
        client.post("/api/v1/auth/forgot-password", json={"email": CONTA})

        resposta = client.post(
            "/api/v1/auth/reset-password",
            json={"token": _token_do_email(email), "password": NOVA},
        )

        assert resposta.status_code == 200
        assert resposta.json() == {"email": CONTA, "role": "admin"}
        assert client.get("/api/v1/auth/me").status_code == 200

    def test_a_senha_antiga_deixa_de_valer_e_a_nova_vale(self) -> None:
        client, email, _ = _app_com_conta()
        client.post("/api/v1/auth/forgot-password", json={"email": CONTA})
        client.post(
            "/api/v1/auth/reset-password",
            json={"token": _token_do_email(email), "password": NOVA},
        )
        client.cookies.clear()

        antiga = client.post("/api/v1/auth/login", json={"email": CONTA, "password": SENHA})
        nova = client.post("/api/v1/auth/login", json={"email": CONTA, "password": NOVA})

        assert antiga.status_code == 401
        assert nova.status_code == 200

    def test_derruba_as_sessoes_abertas_em_outros_dispositivos(self) -> None:
        """Quem usa o reset é frequentemente quem teve a conta tomada: a sessão do
        invasor tem de morrer junto."""
        client, email, _ = _app_com_conta()
        # Outro "dispositivo": segundo cliente na MESMA aplicação, com sessão própria.
        outro = tls_client(client.app)  # type: ignore[arg-type]
        outro.post("/api/v1/auth/login", json={"email": CONTA, "password": SENHA})
        assert outro.get("/api/v1/auth/me").status_code == 200

        client.post("/api/v1/auth/forgot-password", json={"email": CONTA})
        client.post(
            "/api/v1/auth/reset-password",
            json={"token": _token_do_email(email), "password": NOVA},
        )

        assert outro.get("/api/v1/auth/me").status_code == 401

    def test_o_mesmo_link_nao_serve_duas_vezes(self) -> None:
        client, email, _ = _app_com_conta()
        client.post("/api/v1/auth/forgot-password", json={"email": CONTA})
        token = _token_do_email(email)
        client.post("/api/v1/auth/reset-password", json={"token": token, "password": NOVA})

        resposta = client.post(
            "/api/v1/auth/reset-password",
            json={"token": token, "password": "terceira-senha-comprida"},
        )

        assert resposta.status_code == 410
        assert resposta.json()["detail"]["code"] == "reset_used"

    def test_senha_curta_e_422(self) -> None:
        client, email, _ = _app_com_conta()
        client.post("/api/v1/auth/forgot-password", json={"email": CONTA})

        resposta = client.post(
            "/api/v1/auth/reset-password",
            json={"token": _token_do_email(email), "password": "curta"},
        )

        assert resposta.status_code == 422


class TestVerdictFor:
    """A precedência é decisão de produto, e é o que faz a tela dizer o que fazer em
    seguida. Testada isolada porque é pura."""

    AGORA = datetime.now(UTC)

    def _carimbos(
        self,
        *,
        expires_at: datetime | None = None,
        used_at: datetime | None = None,
        superseded_at: datetime | None = None,
    ) -> ResetStamps:
        return ResetStamps(
            email="a@b.invalido",
            expires_at=expires_at or (self.AGORA + timedelta(hours=1)),
            used_at=used_at,
            superseded_at=superseded_at,
        )

    def test_pendente_e_none(self) -> None:
        assert verdict_for(self._carimbos(), now=self.AGORA) is None

    def test_sem_linha_e_404(self) -> None:
        assert verdict_for(None, now=self.AGORA) == (404, "reset_not_found")

    def test_usado_vence_substituido_e_vencido(self) -> None:
        """Um link usado já virou senha nova: mandar "venceu" faria a pessoa pedir
        outro link em vez de fazer login."""
        carimbos = self._carimbos(
            used_at=self.AGORA,
            superseded_at=self.AGORA,
            expires_at=self.AGORA - timedelta(seconds=1),
        )

        assert verdict_for(carimbos, now=self.AGORA) == (410, "reset_used")

    def test_substituido_vence_vencido(self) -> None:
        """Há um sucessor vivo na caixa de entrada, e dizer isso é acionável."""
        carimbos = self._carimbos(
            superseded_at=self.AGORA, expires_at=self.AGORA - timedelta(seconds=1)
        )

        assert verdict_for(carimbos, now=self.AGORA) == (410, "reset_superseded")

    def test_vencido_e_410(self) -> None:
        carimbos = self._carimbos(expires_at=self.AGORA)

        assert verdict_for(carimbos, now=self.AGORA) == (410, "reset_expired")

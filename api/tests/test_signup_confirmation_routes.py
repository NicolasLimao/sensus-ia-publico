"""O cadastro em dois passos: pedir, conferir o link, confirmar.

A propriedade que esta fatia existe para garantir — nenhuma resposta distingue endereço
livre de endereço com conta — está em `TestOCadastroNaoRevela`. As peças puras vêm
primeiro: elas são texto de produto e derivação de estado, e não precisam de HTTP.
"""

import re
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from conftest import settings_sem_env, tls_client
from sensus_api.app import create_app
from sensus_api.email_memory import RecordingEmailSender
from sensus_api.pending_signups import SignupStamps
from sensus_api.routes.signup_confirmation import (
    _token_hash,
    already_registered_email,
    confirmation_email,
    verdict_for,
)

AGORA = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
SENHA = "senha-bem-longa"
TOKEN = "token-de-teste-sem-entropia-nenhuma"


def _stamps(
    *,
    organization_name: str = "Acme",
    email: str = "nova@exemplo.invalido",
    expires_at: datetime | None = None,
    consumed_at: datetime | None = None,
    superseded_at: datetime | None = None,
) -> SignupStamps:
    return SignupStamps(
        organization_name=organization_name,
        email=email,
        expires_at=expires_at if expires_at is not None else AGORA + timedelta(days=1),
        consumed_at=consumed_at,
        superseded_at=superseded_at,
    )


class TestVerdictFor:
    def test_pendente_nao_tem_veredito(self) -> None:
        assert verdict_for(_stamps(), now=AGORA) is None

    def test_sem_linha_e_404(self) -> None:
        assert verdict_for(None, now=AGORA) == (404, "signup_not_found")

    def test_consumido_e_410_e_manda_fazer_login(self) -> None:
        assert verdict_for(_stamps(consumed_at=AGORA), now=AGORA) == (410, "signup_used")

    def test_substituido_e_410(self) -> None:
        assert verdict_for(_stamps(superseded_at=AGORA), now=AGORA) == (
            410,
            "signup_superseded",
        )

    def test_vencido_e_410(self) -> None:
        assert verdict_for(_stamps(expires_at=AGORA), now=AGORA) == (
            410,
            "signup_expired",
        )

    def test_consumido_vence_substituido_e_vencido(self) -> None:
        """A precedência não é arbitrária: cada degrau muda o que a tela manda fazer.
        Dizer "venceu" a um link já usado mandaria a pessoa pedir outro link quando ela
        só precisa entrar."""
        stamps = _stamps(
            consumed_at=AGORA,
            superseded_at=AGORA,
            expires_at=AGORA - timedelta(days=1),
        )

        assert verdict_for(stamps, now=AGORA) == (410, "signup_used")


class TestEmails:
    def test_o_email_de_confirmacao_traz_o_link_e_o_prazo(self) -> None:
        mensagem = confirmation_email(
            to="nova@exemplo.invalido",
            link="https://sensus.invalido/confirmar#tok",
            expires_at=AGORA + timedelta(days=1),
        )

        assert mensagem.to == "nova@exemplo.invalido"
        assert "https://sensus.invalido/confirmar#tok" in mensagem.body
        assert "21/09/2026" in mensagem.body

    def test_o_email_de_confirmacao_nao_cita_a_organizacao(self) -> None:
        """Decisão 5 da spec: o nome é texto escolhido por quem digitou o formulário, e
        o e-mail pode chegar à caixa de outra pessoa. Ele aparece na TELA, depois do
        clique. Esta função nem recebe o nome — o teste guarda a assinatura."""
        mensagem = confirmation_email(
            to="nova@exemplo.invalido",
            link="https://sensus.invalido/confirmar#tok",
            expires_at=AGORA + timedelta(days=1),
        )

        assert "Acme" not in mensagem.body
        assert "Acme" not in mensagem.subject

    def test_o_email_de_ja_cadastrado_nao_tem_link(self) -> None:
        """É o que impede a rota anônima de virar disparador de reset para endereços
        alheios: quem já tem conta recebe instrução, não credencial."""
        mensagem = already_registered_email(to="dona@exemplo.invalido")

        assert "http" not in mensagem.body
        assert "#" not in mensagem.body
        assert "senha" in mensagem.body.lower()


# --- As rotas -----------------------------------------------------------------------
# A aplicação é montada à mão, e não pela fixture `app`, porque estes testes precisam de
# `public_base_url` (o link sai dela) e do provedor que grava as mensagens. A pendência
# é criada DENTRO de cada teste, que já roda em loop.


def _app() -> tuple[TestClient, FastAPI]:
    app = create_app(
        settings_sem_env(
            session_secret="segredo-de-teste",
            public_base_url="https://sensus.invalido",
        ),
        email=RecordingEmailSender(),
    )
    return tls_client(app), app


async def _pendencia(
    app: FastAPI,
    *,
    organization_name: str = "Acme",
    email: str = "nova@exemplo.invalido",
    token: str = TOKEN,
    expires_in: int = 86400,
) -> None:
    """O pedido entra pela PORTA, não pela rota: esta task ainda não mudou o
    `POST /auth/signup`."""
    await app.state.pending_signups.request(
        organization_name=organization_name,
        email=email,
        token_hash=_token_hash(token),
        expires_at=datetime.now(UTC) + timedelta(seconds=expires_in),
        throttle_seconds=120,
    )


class TestPreview:
    async def test_link_pendente_devolve_organizacao_e_email(self) -> None:
        client, app = _app()
        await _pendencia(app)

        resposta = client.post("/api/v1/auth/confirm-signup/preview", json={"token": TOKEN})

        assert resposta.status_code == 200
        assert resposta.json() == {
            "organization_name": "Acme",
            "email": "nova@exemplo.invalido",
        }

    async def test_token_desconhecido_e_404(self) -> None:
        client, _ = _app()

        resposta = client.post("/api/v1/auth/confirm-signup/preview", json={"token": "nao-existe"})

        assert resposta.status_code == 404
        assert resposta.json()["detail"]["code"] == "signup_not_found"

    async def test_link_vencido_e_410(self) -> None:
        client, app = _app()
        await _pendencia(app, expires_in=-1)

        resposta = client.post("/api/v1/auth/confirm-signup/preview", json={"token": TOKEN})

        assert resposta.status_code == 410
        assert resposta.json()["detail"]["code"] == "signup_expired"


class TestConfirm:
    async def test_confirmar_cria_a_organizacao_e_ja_entra(self) -> None:
        client, app = _app()
        await _pendencia(app)

        resposta = client.post(
            "/api/v1/auth/confirm-signup", json={"token": TOKEN, "password": SENHA}
        )

        assert resposta.status_code == 201, resposta.text
        assert resposta.json() == {"email": "nova@exemplo.invalido", "role": "admin"}
        # O cookie já veio: a pessoa não passa pela tela de login depois de confirmar.
        me = client.get("/api/v1/auth/me")
        assert me.status_code == 200
        assert me.json()["email"] == "nova@exemplo.invalido"

    async def test_a_senha_confirmada_e_a_que_entra_no_login(self) -> None:
        client, app = _app()
        await _pendencia(app)
        client.post("/api/v1/auth/confirm-signup", json={"token": TOKEN, "password": SENHA})
        client.cookies.clear()

        resposta = client.post(
            "/api/v1/auth/login",
            json={"email": "nova@exemplo.invalido", "password": SENHA},
        )

        assert resposta.status_code == 200

    async def test_confirmar_duas_vezes_responde_410(self) -> None:
        client, app = _app()
        await _pendencia(app)
        client.post("/api/v1/auth/confirm-signup", json={"token": TOKEN, "password": SENHA})

        resposta = client.post(
            "/api/v1/auth/confirm-signup", json={"token": TOKEN, "password": SENHA}
        )

        assert resposta.status_code == 410
        assert resposta.json()["detail"]["code"] == "signup_used"

    async def test_endereco_que_ganhou_conta_no_meio_tempo_e_409(self) -> None:
        """Aqui o 409 é legítimo, e a spec (§3.7) diz por quê: quem apresenta o token é
        o dono da caixa, e o fato é sobre o próprio endereço dele."""
        client, app = _app()
        await _pendencia(app)
        await app.state.users.create_organization_with_admin(
            organization_name="Globex",
            email="nova@exemplo.invalido",
            password_hash="hash",
        )

        resposta = client.post(
            "/api/v1/auth/confirm-signup", json={"token": TOKEN, "password": SENHA}
        )

        assert resposta.status_code == 409
        assert resposta.json()["detail"]["code"] == "email_taken"

    async def test_senha_curta_e_recusada_antes_de_consumir_o_link(self) -> None:
        client, app = _app()
        await _pendencia(app)

        resposta = client.post(
            "/api/v1/auth/confirm-signup", json={"token": TOKEN, "password": "curta"}
        )

        assert resposta.status_code == 422
        stamps = await app.state.pending_signups.stamps_by_token_hash(_token_hash(TOKEN))
        assert stamps is not None and stamps.consumed_at is None


class TestOCadastroNaoRevela:
    """A propriedade que esta fatia existe para garantir.

    Herdeiro do `test_NENHUMA_resposta_contem_o_token` da 3e: compara as respostas
    inteiras, e não um campo escolhido a dedo. Nenhum tipo pega um 409 que volte por
    engano num refactor.
    """

    async def test_endereco_livre_e_endereco_com_conta_respondem_IGUAL(self) -> None:
        client, app = _app()
        await app.state.users.create_organization_with_admin(
            organization_name="Globex", email="dona@exemplo.invalido", password_hash="h"
        )

        livre = client.post(
            "/api/v1/auth/signup",
            json={"organization_name": "Acme", "email": "nova@exemplo.invalido"},
        )
        ocupado = client.post(
            "/api/v1/auth/signup",
            json={"organization_name": "Acme", "email": "dona@exemplo.invalido"},
        )

        assert livre.status_code == 202
        assert ocupado.status_code == livre.status_code
        assert ocupado.json() == livre.json()

    async def test_o_cadastro_nao_emite_cookie_de_sessao(self) -> None:
        """Não há conta ainda. Um cookie aqui seria sessão de quem não existe."""
        client, _ = _app()

        resposta = client.post(
            "/api/v1/auth/signup",
            json={"organization_name": "Acme", "email": "nova@exemplo.invalido"},
        )

        assert resposta.status_code == 202
        assert "set-cookie" not in {chave.lower() for chave in resposta.headers}
        assert client.get("/api/v1/auth/me").status_code == 401

    async def test_endereco_livre_recebe_o_link_por_email(self) -> None:
        client, app = _app()

        client.post(
            "/api/v1/auth/signup",
            json={"organization_name": "Acme", "email": "nova@exemplo.invalido"},
        )

        mensagem = app.state.email.sent[-1]
        assert mensagem.to == "nova@exemplo.invalido"
        assert "https://sensus.invalido/confirmar#" in mensagem.body

    async def test_endereco_com_conta_recebe_OUTRO_email_sem_link(self) -> None:
        client, app = _app()
        await app.state.users.create_organization_with_admin(
            organization_name="Globex", email="dona@exemplo.invalido", password_hash="h"
        )

        client.post(
            "/api/v1/auth/signup",
            json={"organization_name": "Acme", "email": "dona@exemplo.invalido"},
        )

        mensagem = app.state.email.sent[-1]
        assert mensagem.to == "dona@exemplo.invalido"
        assert "http" not in mensagem.body

    async def test_o_token_so_existe_dentro_do_email(self) -> None:
        """A resposta não o carrega, e nenhuma rota o devolve: a única cópia está na
        caixa de entrada. É isso que faz a confirmação provar posse do endereço."""
        client, app = _app()

        resposta = client.post(
            "/api/v1/auth/signup",
            json={"organization_name": "Acme", "email": "nova@exemplo.invalido"},
        )

        achado = re.search(r"#([A-Za-z0-9_-]+)", app.state.email.sent[-1].body)
        assert achado is not None
        assert achado.group(1) not in resposta.text

    async def test_o_pedido_freado_responde_igual_e_nao_manda_segundo_email(
        self,
    ) -> None:
        client, app = _app()
        dados = {"organization_name": "Acme", "email": "nova@exemplo.invalido"}

        primeiro = client.post("/api/v1/auth/signup", json=dados)
        segundo = client.post("/api/v1/auth/signup", json=dados)

        assert segundo.status_code == primeiro.status_code
        assert segundo.json() == primeiro.json()
        assert len(app.state.email.sent) == 1

    async def test_o_cadastro_nao_aceita_mais_senha(self) -> None:
        """A senha nasce na confirmação. Um campo extra aceito em silêncio faria o front
        antigo parecer funcionar enquanto a senha ia para o lixo."""
        client, _ = _app()

        resposta = client.post(
            "/api/v1/auth/signup",
            json={
                "organization_name": "Acme",
                "email": "nova@exemplo.invalido",
                "password": "senha-bem-longa",
            },
        )

        assert resposta.status_code == 422

    async def test_o_caminho_inteiro_cria_a_organizacao(self) -> None:
        client, app = _app()
        client.post(
            "/api/v1/auth/signup",
            json={"organization_name": "Acme", "email": "nova@exemplo.invalido"},
        )
        achado = re.search(r"#([A-Za-z0-9_-]+)", app.state.email.sent[-1].body)
        assert achado is not None

        resposta = client.post(
            "/api/v1/auth/confirm-signup",
            json={"token": achado.group(1), "password": SENHA},
        )

        assert resposta.status_code == 201
        assert resposta.json() == {"email": "nova@exemplo.invalido", "role": "admin"}

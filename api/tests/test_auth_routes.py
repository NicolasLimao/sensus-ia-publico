"""Login, logout e `/me`, pela aplicação real.

O cadastro saiu daqui na fatia 3f, junto com a rota: ele virou um pedido em dois
passos e mora em `test_signup_confirmation_routes.py`. O que sobra neste arquivo
prepara a conta pelo helper `cadastrar` e afirma sobre sessão.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from conftest import cadastrar, settings_sem_env, tls_client
from sensus_api.users_memory import InMemoryUserDirectory

# Sem senha desde a fatia 3f: ela nasce na confirmação, não no cadastro.
CADASTRO = {"organization_name": "Acme", "email": "ana@acme.com"}


def test_o_cookie_do_cadastro_e_httponly_e_samesite_lax(client: TestClient, app: FastAPI) -> None:
    """O cookie é a sessão inteira: sem httpOnly, um XSS a rouba."""
    resposta = cadastrar(client, app, **CADASTRO)

    cookie = resposta.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "samesite=lax" in cookie.lower()


def test_o_cookie_e_secure(client: TestClient, app: FastAPI) -> None:
    """Sem `Secure`, uma única requisição a `http://` entrega o token inteiro em
    claro — e o token É a sessão."""
    resposta = cadastrar(client, app, **CADASTRO)

    assert "secure" in resposta.headers["set-cookie"].lower()


def test_o_default_do_secure_e_ligado() -> None:
    """Quem desliga é o dev local, explicitamente. Construído sem `.env` de propósito:
    o default que importa é o do código, não o da máquina de quem roda o teste."""
    assert settings_sem_env(session_secret="x").session_cookie_secure is True


def test_o_secure_pode_ser_desligado_para_o_dev_local(
    client_sem_tls: TestClient, app: FastAPI
) -> None:
    """`http://localhost` não tem TLS: com `Secure`, o navegador descarta o cookie e o
    dev não consegue nem entrar."""
    resposta = cadastrar(client_sem_tls, app, **CADASTRO)

    assert "secure" not in resposta.headers["set-cookie"].lower()


def test_entrar_de_novo_depois_de_sair_vale(client: TestClient, app: FastAPI) -> None:
    """O cookie do login precisa carregar o contador CORRENTE da conta, não um valor
    parado. `_emitir_cookie` assina `account.session_epoch` — se assinasse um valor
    fixo (por exemplo `0`), o cookie emitido aqui divergiria do da conta em qualquer
    pessoa que já tivesse saído uma vez, e ninguém que já deslogou conseguiria voltar
    a entrar. Só chamar `/login` e conferir 200 não prova isso: prova a rota, não o
    cookie — é o `/auth/me` com o cookie NOVO que fecha o teste.
    """
    cadastrar(client, app, **CADASTRO)
    client.post("/api/v1/auth/logout")

    resposta = client.post(
        "/api/v1/auth/login", json={"email": "ana@acme.com", "password": "senha-bem-longa"}
    )
    assert resposta.status_code == 200

    assert client.get("/api/v1/auth/me").status_code == 200


def test_login_com_senha_certa(client: TestClient, app: FastAPI) -> None:
    cadastrar(client, app, **CADASTRO)
    client.post("/api/v1/auth/logout")

    resposta = client.post(
        "/api/v1/auth/login", json={"email": "ana@acme.com", "password": "senha-bem-longa"}
    )

    assert resposta.status_code == 200
    assert resposta.json()["email"] == "ana@acme.com"


def test_senha_errada_e_email_inexistente_sao_indistinguiveis(
    client: TestClient, app: FastAPI
) -> None:
    """Respostas diferentes entregariam quais e-mails têm conta."""
    cadastrar(client, app, **CADASTRO)
    client.post("/api/v1/auth/logout")

    errada = client.post(
        "/api/v1/auth/login", json={"email": "ana@acme.com", "password": "outra-senha-longa"}
    )
    inexistente = client.post(
        "/api/v1/auth/login",
        json={"email": "ninguem@acme.com", "password": "outra-senha-longa"},
    )

    assert errada.status_code == inexistente.status_code == 401
    assert errada.json() == inexistente.json()


def test_logout_derruba_a_sessao(client: TestClient, app: FastAPI) -> None:
    cadastrar(client, app, **CADASTRO)

    client.post("/api/v1/auth/logout")

    assert client.get("/api/v1/auth/me").status_code == 401


def test_me_devolve_o_que_o_front_precisa(client: TestClient, app: FastAPI) -> None:
    cadastrar(client, app, **CADASTRO)

    corpo = client.get("/api/v1/auth/me").json()

    assert corpo["email"] == "ana@acme.com"
    assert corpo["role"] == "admin"
    assert "suggestions" in corpo


def test_me_sem_sessao_e_401(client: TestClient) -> None:
    assert client.get("/api/v1/auth/me").status_code == 401


def test_o_token_de_antes_do_logout_deixa_de_valer(client: TestClient, app: FastAPI) -> None:
    """O teste que define a fatia. Apagar o cookie do navegador não encerra nada: quem
    tiver uma cópia do token — extensão, log de proxy, máquina compartilhada — entrava
    com ela pelos 7 dias do prazo, depois de a pessoa ter saído."""
    cadastrar(client, app, **CADASTRO)
    token = client.cookies.get("sensus_session")
    assert token

    client.post("/api/v1/auth/logout")
    client.cookies.set("sensus_session", token)

    assert client.get("/api/v1/auth/me").status_code == 401


def test_sair_encerra_as_outras_sessoes_da_mesma_pessoa(client: TestClient, app: FastAPI) -> None:
    """Consequência deliberada do contador ser por usuário, e não por sessão: sair no
    celular desconecta o desktop. Está aqui como asserção para não virar surpresa."""
    cadastrar(client, app, **CADASTRO)
    with tls_client(app) as outro_dispositivo:
        entrada = outro_dispositivo.post(
            "/api/v1/auth/login",
            json={"email": "ana@acme.com", "password": "senha-bem-longa"},
        )
        assert entrada.status_code == 200
        assert outro_dispositivo.get("/api/v1/auth/me").status_code == 200

        client.post("/api/v1/auth/logout")

        assert outro_dispositivo.get("/api/v1/auth/me").status_code == 401


def test_logout_sem_sessao_continua_204(client: TestClient) -> None:
    """O botão "Sair" não tem outra saída além de esquecer a sessão; erro ali não
    serve a ninguém, e o `logout()` do front engole falha de propósito."""
    assert client.post("/api/v1/auth/logout").status_code == 204


def test_logout_com_token_ilegivel_continua_204(client: TestClient) -> None:
    client.cookies.set("sensus_session", "nao-e-um-token")

    assert client.post("/api/v1/auth/logout").status_code == 204


class _BumpSessionEpochFalha(InMemoryUserDirectory):
    """Duplo cujo `bump_session_epoch` sempre levanta — simula o erro transiente de
    banco que a rota de logout precisa sobreviver sem deixar o cookie vivo."""

    async def bump_session_epoch(self, user_id: str) -> None:
        raise RuntimeError("falha simulada de banco")


def test_logout_com_falha_no_bump_ainda_apaga_o_cookie_e_e_204(app: FastAPI) -> None:
    """Se `bump_session_epoch` falhar (erro transiente do Postgres), a rota não pode
    responder 500 nem deixar o cookie vivo: 500 quebra o contrato do §3 da spec
    ("204 sempre"), e um cookie vivo seria PIOR que o comportamento que esta fatia
    substituiu — a pessoa veria a tela de login com o navegador ainda guardando um
    cookie válido para uma sessão que o servidor nunca encerrou."""
    with tls_client(app) as client:
        cadastrar(client, app, **CADASTRO)
        assert isinstance(app.state.users, InMemoryUserDirectory)
        app.state.users = _BumpSessionEpochFalha(accounts=dict(app.state.users.accounts))

        resposta = client.post("/api/v1/auth/logout")

        assert resposta.status_code == 204
        cookie = resposta.headers["set-cookie"]
        assert cookie.startswith('sensus_session=""')
        assert "max-age=0" in cookie.lower()

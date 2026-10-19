"""As rotas de convite, pela aplicação real.

O cadastro sempre cria `admin`; rebaixar a conta no duplo é como se exercita o `member`
sem inventar uma rota de troca de papel que esta fatia não tem — mesmo padrão de
`test_documents_route.py`.
"""

import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from conftest import cadastrar, settings_sem_env, tls_client
from sensus_api.app import create_app
from sensus_api.email_memory import RecordingEmailSender
from sensus_api.invitations import InvitationStamps
from sensus_api.routes.invites import invite_email, verdict_for

UM_UUID = "11111111-1111-1111-1111-111111111111"


def _rebaixar(app: FastAPI, email: str = "ana@acme.com") -> None:
    directory = app.state.users
    directory.accounts[email] = replace(directory.accounts[email], role="member")


# O endereço convidado por default. Desde a 3e o convite é PARA alguém, e quem cria dois
# convites no mesmo teste precisa de endereços distintos: criar para o mesmo endereço
# revoga o pendente anterior.
CONVIDADA = "bob@acme.com"
OUTRA = "carol@acme.com"


@pytest.fixture(autouse=True)
def gravador(app: FastAPI) -> RecordingEmailSender:
    """Troca o provedor da aplicação por um que guarda as mensagens.

    Autouse porque desde a 3e não há convite sem envio: a resposta da criação não traz
    mais o token, e o único lugar onde ele existe é a caixa de entrada. Todo teste que
    precisa de um link o lê daqui — como a pessoa convidada faria.
    """
    mensagens = RecordingEmailSender()
    app.state.email = mensagens
    return mensagens


def _token_do_email(email: RecordingEmailSender) -> str:
    achado = re.search(r"#([A-Za-z0-9_-]+)", email.sent[-1].body)
    assert achado is not None, f"nenhum token no corpo: {email.sent[-1].body}"
    return achado.group(1)


def _criar(client: TestClient, email: str = CONVIDADA) -> dict[str, str]:
    """Cria o convite e devolve o corpo da resposta MAIS o token lido do e-mail.

    O `token` da chave não vem da resposta — ela não o traz desde a 3e. Vem da mensagem
    que acabou de ser enviada, que é o único lugar onde ele existe.
    """
    resposta = client.post("/api/v1/invites", json={"email": email})
    assert resposta.status_code == 201, resposta.text
    corpo: dict[str, str] = resposta.json()
    return {**corpo, "token": _token_do_email(client.app.state.email)}  # type: ignore[attr-defined]


def test_a_criacao_devolve_id_endereco_e_prazo(session_client: TestClient) -> None:
    """O que o admin precisa saber: para quem foi, e até quando vale. O token não está
    aqui — ele existe só dentro do e-mail (ver `test_NENHUMA_resposta_contem_o_token`).
    """
    corpo = _criar(session_client)

    assert corpo["id"]
    assert corpo["email"] == CONVIDADA
    assert corpo["expires_at"]
    pendentes = session_client.get("/api/v1/invites").json()
    assert pendentes[0]["id"] == corpo["id"]


def test_cada_convite_ganha_um_token_proprio(session_client: TestClient) -> None:
    """Dois convites, dois links — e cada link só existe na caixa de quem o recebeu."""
    assert _criar(session_client)["token"] != _criar(session_client, OUTRA)["token"]


def test_o_prazo_do_convite_sai_das_settings(session_client: TestClient) -> None:
    corpo = _criar(session_client)

    expira = datetime.fromisoformat(corpo["expires_at"])
    daqui_a_sete_dias = datetime.now(UTC) + timedelta(days=7)
    assert expira > datetime.now(UTC)
    assert abs((expira - daqui_a_sete_dias).total_seconds()) < 60


def test_o_default_do_prazo_e_o_mesmo_da_sessao() -> None:
    """Espelhar o prazo da sessão é um relógio a menos no produto. Construído sem
    `.env` de propósito: o default que importa é o do código, não o da máquina."""
    settings = settings_sem_env(session_secret="x")
    assert settings.invite_max_age_seconds == 7 * 24 * 60 * 60
    assert settings.invite_max_age_seconds == settings.session_max_age_seconds


def test_a_listagem_traz_o_mais_recente_primeiro(session_client: TestClient) -> None:
    primeiro = _criar(session_client)
    segundo = _criar(session_client, OUTRA)

    pendentes = session_client.get("/api/v1/invites").json()

    assert [p["id"] for p in pendentes] == [segundo["id"], primeiro["id"]]


def test_revogar_tira_o_convite_da_lista(session_client: TestClient) -> None:
    criado = _criar(session_client)

    resposta = session_client.delete(f"/api/v1/invites/{criado['id']}")

    assert resposta.status_code == 204
    assert session_client.get("/api/v1/invites").json() == []


def test_revogar_o_que_nao_existe_tambem_e_204(session_client: TestClient) -> None:
    """Idempotente: quem clicou duas vezes em "Revogar" não merece um erro."""
    assert session_client.delete(f"/api/v1/invites/{UM_UUID}").status_code == 204


def test_revogar_com_id_que_nao_e_uuid_e_422(session_client: TestClient) -> None:
    """Sem o tipo no path, uma string qualquer chegaria ao Postgres e viraria 500."""
    assert session_client.delete("/api/v1/invites/nao-e-uuid").status_code == 422


def test_um_admin_nao_ve_o_convite_do_outro(
    session_client: TestClient, other_session_client: TestClient
) -> None:
    criado = _criar(session_client)

    assert other_session_client.get("/api/v1/invites").json() == []
    assert other_session_client.delete(f"/api/v1/invites/{criado['id']}").status_code == 204
    # A metade positiva: o convite do dono sobreviveu à revogação do vizinho.
    assert [p["id"] for p in session_client.get("/api/v1/invites").json()] == [criado["id"]]


def test_membro_nao_cria_convite(session_client: TestClient, app: FastAPI) -> None:
    """Convidar muda quem lê a base da organização inteira — mesma régua do upload."""
    _rebaixar(app)

    resposta = session_client.post("/api/v1/invites", json={"email": CONVIDADA})

    assert resposta.status_code == 403
    assert resposta.json()["detail"]["code"] == "forbidden_role"


def test_membro_nao_lista_nem_revoga_convite(session_client: TestClient, app: FastAPI) -> None:
    _rebaixar(app)

    assert session_client.get("/api/v1/invites").status_code == 403
    assert session_client.delete(f"/api/v1/invites/{UM_UUID}").status_code == 403


def test_sem_sessao_nao_se_convida_ninguem(client: TestClient) -> None:
    assert client.post("/api/v1/invites", json={"email": CONVIDADA}).status_code == 401
    assert client.get("/api/v1/invites").status_code == 401
    assert client.delete(f"/api/v1/invites/{UM_UUID}").status_code == 401


# --- O veredito, como função pura ------------------------------------------------


def _carimbos(
    *,
    expires_at: datetime | None = None,
    accepted_at: datetime | None = None,
    revoked_at: datetime | None = None,
) -> InvitationStamps:
    return InvitationStamps(
        organization_name="Acme",
        email=CONVIDADA,
        expires_at=expires_at or (datetime.now(UTC) + timedelta(hours=1)),
        accepted_at=accepted_at,
        revoked_at=revoked_at,
    )


def test_convite_pendente_nao_tem_veredito() -> None:
    assert verdict_for(_carimbos(), now=datetime.now(UTC)) is None


def test_sem_linha_o_veredito_e_404() -> None:
    assert verdict_for(None, now=datetime.now(UTC)) == (404, "invite_not_found")


def test_os_tres_motivos_de_410_sao_distinguidos() -> None:
    agora = datetime.now(UTC)

    assert verdict_for(_carimbos(accepted_at=agora), now=agora) == (410, "invite_used")
    assert verdict_for(_carimbos(revoked_at=agora), now=agora) == (410, "invite_revoked")
    assert verdict_for(_carimbos(expires_at=agora - timedelta(seconds=1)), now=agora) == (
        410,
        "invite_expired",
    )


def test_aceito_tem_precedencia_sobre_revogado_e_vencido() -> None:
    """Um convite aceito e depois vencido já virou uma pessoa na organização: dizer
    "venceu" mandaria essa pessoa pedir outro link em vez de fazer login."""
    agora = datetime.now(UTC)

    veredito = verdict_for(
        _carimbos(
            accepted_at=agora - timedelta(days=9),
            revoked_at=agora,
            expires_at=agora - timedelta(days=2),
        ),
        now=agora,
    )

    assert veredito == (410, "invite_used")


# --- Prévia ----------------------------------------------------------------------


def test_a_previa_diz_para_qual_organizacao_a_pessoa_foi_convidada(
    session_client: TestClient, client: TestClient
) -> None:
    criado = _criar(session_client)

    resposta = client.post("/api/v1/invites/preview", json={"token": criado["token"]})

    assert resposta.status_code == 200
    assert resposta.json()["organization_name"] == "Acme"
    assert resposta.json()["expires_at"] == criado["expires_at"]


def test_a_previa_nao_aceita_o_token_na_url(client: TestClient) -> None:
    """O token é a credencial: num GET ele entraria no log de acesso, nos proxies do
    caminho e no `Referer`. A prévia é POST por isso, e não por gosto."""
    assert client.get("/api/v1/invites/preview").status_code == 405


def test_previa_de_token_desconhecido_e_404(client: TestClient) -> None:
    resposta = client.post("/api/v1/invites/preview", json={"token": "nao-existe"})

    assert resposta.status_code == 404
    assert resposta.json()["detail"]["code"] == "invite_not_found"


def test_previa_de_convite_revogado_diz_que_foi_revogado(
    session_client: TestClient, client: TestClient
) -> None:
    criado = _criar(session_client)
    session_client.delete(f"/api/v1/invites/{criado['id']}")

    resposta = client.post("/api/v1/invites/preview", json={"token": criado["token"]})

    assert resposta.status_code == 410
    assert resposta.json()["detail"]["code"] == "invite_revoked"


def test_previa_de_convite_ja_usado_diz_que_foi_usado(
    session_client: TestClient, client: TestClient, app: FastAPI
) -> None:
    criado = _criar(session_client)
    # Um terceiro cliente na MESMA aplicação: quem aceitou não é quem está pedindo a
    # prévia agora, e `client` precisa continuar sem cookie para o resto do teste.
    with tls_client(app) as convidado:
        convidado.post(
            "/api/v1/invites/accept",
            json={"token": criado["token"], "password": "senha-bem-longa"},
        )

    resposta = client.post("/api/v1/invites/preview", json={"token": criado["token"]})

    assert resposta.status_code == 410
    assert resposta.json()["detail"]["code"] == "invite_used"


# --- Aceite ----------------------------------------------------------------------


def test_aceitar_entra_como_member_na_organizacao_de_quem_convidou(
    session_client: TestClient, client: TestClient, app: FastAPI
) -> None:
    """O caminho inteiro da fatia, pela aplicação."""
    criado = _criar(session_client)

    resposta = client.post(
        "/api/v1/invites/accept",
        json={"token": criado["token"], "password": "senha-bem-longa"},
    )

    assert resposta.status_code == 201, resposta.text
    assert resposta.json() == {"email": "bob@acme.com", "role": "member"}
    # Caiu logado: o cookie veio na resposta e o /auth/me já responde por ele.
    assert client.cookies.get("sensus_session")
    eu = client.get("/api/v1/auth/me")
    assert eu.status_code == 200
    assert eu.json()["email"] == "bob@acme.com"
    # E na organização de quem convidou, não numa nova.
    contas = app.state.users.accounts
    assert contas["bob@acme.com"].tenant_id == contas["ana@acme.com"].tenant_id


def test_o_cookie_do_aceite_e_httponly_e_secure(
    session_client: TestClient, client: TestClient
) -> None:
    """Mesmas garantias do cadastro: o cookie É a sessão."""
    criado = _criar(session_client)

    resposta = client.post(
        "/api/v1/invites/accept",
        json={"token": criado["token"], "password": "senha-bem-longa"},
    )

    cookie = resposta.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "secure" in cookie.lower()
    assert "samesite=lax" in cookie.lower()


def test_aceitar_com_outra_sessao_aberta_troca_de_conta(session_client: TestClient) -> None:
    """Um navegador, uma sessão. A sessão existente é ignorada, e o cookie novo a
    substitui — quem aceitou um convite quer entrar como a conta nova."""
    criado = _criar(session_client)

    resposta = session_client.post(
        "/api/v1/invites/accept",
        json={"token": criado["token"], "password": "senha-bem-longa"},
    )

    assert resposta.status_code == 201
    assert session_client.get("/api/v1/auth/me").json()["email"] == "bob@acme.com"


def test_o_mesmo_link_nao_serve_duas_vezes(session_client: TestClient, client: TestClient) -> None:
    criado = _criar(session_client)
    corpo = {"token": criado["token"], "password": "senha-bem-longa"}
    client.post("/api/v1/invites/accept", json=corpo)

    # Sem campo de e-mail para trocar: o segundo clique é o MESMO pedido, e o que o
    # barra é o convite já consumido.
    segunda = client.post("/api/v1/invites/accept", json=corpo)

    assert segunda.status_code == 410
    assert segunda.json()["detail"]["code"] == "invite_used"


def test_aceitar_convite_revogado_e_410(session_client: TestClient, client: TestClient) -> None:
    criado = _criar(session_client)
    session_client.delete(f"/api/v1/invites/{criado['id']}")

    resposta = client.post(
        "/api/v1/invites/accept",
        json={"token": criado["token"], "password": "senha-bem-longa"},
    )

    assert resposta.status_code == 410
    assert resposta.json()["detail"]["code"] == "invite_revoked"


def test_aceitar_token_desconhecido_e_404(client: TestClient) -> None:
    resposta = client.post(
        "/api/v1/invites/accept",
        json={"token": "nao-existe", "password": "senha-bem-longa"},
    )

    assert resposta.status_code == 404
    assert resposta.json()["detail"]["code"] == "invite_not_found"


def test_email_que_ja_tem_conta_e_409_e_o_convite_continua_valendo(
    session_client: TestClient, other_session_client: TestClient, client: TestClient
) -> None:
    """Limitação conhecida, e desde a 3e ela sobrou só para conta de OUTRA organização:
    na própria, a borda barra na criação. Destino: 3f. O que NÃO pode acontecer é a
    tentativa queimar o link — o convite continua pendente.

    `other_session_client` existe aqui só para dar conta ao `bob@globex.com`.
    """
    criado = _criar(session_client, "bob@globex.com")

    resposta = client.post(
        "/api/v1/invites/accept",
        json={"token": criado["token"], "password": "senha-bem-longa"},
    )

    assert resposta.status_code == 409
    assert resposta.json()["detail"]["code"] == "email_taken"
    previa = client.post("/api/v1/invites/preview", json={"token": criado["token"]})
    assert previa.status_code == 200, "o convite foi queimado por uma tentativa que falhou"


def test_senha_curta_no_aceite_e_422(session_client: TestClient, client: TestClient) -> None:
    """O mesmo mínimo de 12 do cadastro: entrar por convite não pode ser a porta de
    senha fraca."""
    criado = _criar(session_client)

    resposta = client.post(
        "/api/v1/invites/accept",
        json={"token": criado["token"], "password": "curta"},
    )

    assert resposta.status_code == 422


@pytest.mark.parametrize("corpo", [{}, {"token": ""}])
def test_aceite_sem_token_e_422(client: TestClient, corpo: dict[str, str]) -> None:
    resposta = client.post(
        "/api/v1/invites/accept",
        json={**corpo, "password": "senha-bem-longa"},
    )

    assert resposta.status_code == 422


def test_revogar_convite_ja_aceito_nao_desfaz_ninguem(
    session_client: TestClient, client: TestClient
) -> None:
    """Revogação morde só o pendente. Quem já entrou está na organização, e revogar o
    convite não pode fingir que a tirou de lá — remover alguém é outra fatia."""
    criado = _criar(session_client)
    client.post(
        "/api/v1/invites/accept",
        json={"token": criado["token"], "password": "senha-bem-longa"},
    )

    assert session_client.delete(f"/api/v1/invites/{criado['id']}").status_code == 204

    # A conta continua valendo: o cookie do aceite ainda responde.
    assert client.get("/api/v1/auth/me").json()["email"] == "bob@acme.com"
    # E o convite continua contando como usado, não como revogado.
    previa = client.post("/api/v1/invites/preview", json={"token": criado["token"]})
    assert previa.json()["detail"]["code"] == "invite_used"


# --- O envio, e o que a resposta deixou de trazer ----------------------------------


def _app_de_convite() -> tuple[TestClient, RecordingEmailSender]:
    """Aplicação com admin logado, provedor que grava e base pública configurada.

    Monta a própria aplicação em vez de usar a fixture `app` porque precisa das duas
    últimas — o link do e-mail sai de `public_base_url`, e sem o provedor de gravação
    não há como ler o token que a resposta deixou de devolver.
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
    cadastrar(client, app, organization_name="Acme", email="ana@acme.com")
    # O cadastro também manda e-mail desde a 3f, e aqui ele é ANDAIME: limpar a caixa
    # mantém os testes deste arquivo falando só das mensagens de convite — inclusive os
    # que CONTAM quantas saíram.
    email.sent.clear()
    return client, email


class TestCriarConvite:
    def test_manda_o_e_mail_para_o_endereco_informado(self) -> None:
        client, email = _app_de_convite()

        resposta = client.post("/api/v1/invites", json={"email": "convidada@exemplo.invalido"})

        assert resposta.status_code == 201
        assert resposta.json()["email"] == "convidada@exemplo.invalido"
        assert len(email.sent) == 1
        assert email.sent[0].to == "convidada@exemplo.invalido"

    def test_NENHUMA_resposta_contem_o_token(self) -> None:
        """A propriedade central da fatia. Um campo devolvido por engano a desfaz em
        silêncio, e nenhum tipo pega isso."""
        client, email = _app_de_convite()

        criacao = client.post("/api/v1/invites", json={"email": "convidada@exemplo.invalido"})
        listagem = client.get("/api/v1/invites")
        token = _token_do_email(email)

        assert token not in criacao.text
        assert token not in listagem.text

    def test_o_link_do_e_mail_aponta_para_a_base_publica(self) -> None:
        client, email = _app_de_convite()

        client.post("/api/v1/invites", json={"email": "convidada@exemplo.invalido"})

        assert "https://sensus.invalido/convite#" in email.sent[0].body

    def test_o_e_mail_diz_o_nome_da_organizacao(self) -> None:
        client, email = _app_de_convite()

        client.post("/api/v1/invites", json={"email": "convidada@exemplo.invalido"})

        assert "Acme" in email.sent[0].body

    def test_convidar_quem_ja_e_membro_DESTA_organizacao_e_409(self) -> None:
        client, email = _app_de_convite()

        resposta = client.post("/api/v1/invites", json={"email": "ana@acme.com"})

        assert resposta.status_code == 409
        assert resposta.json()["detail"]["code"] == "already_member"
        assert email.sent == []

    def test_convidar_quem_tem_conta_em_OUTRA_organizacao_funciona(self) -> None:
        """O teste que guarda a decisão contra o oráculo entre tenants. `find_by_email`
        é global — devolver 409 sempre que ela encontrar alguém faria o painel da Acme
        revelar quem tem conta na Globex."""
        client, email = _app_de_convite()
        outra = tls_client(client.app)  # type: ignore[arg-type]
        cadastrar(
            outra,
            client.app,  # type: ignore[arg-type]
            organization_name="Globex",
            email="bob@globex.com",
        )
        email.sent.clear()

        resposta = client.post("/api/v1/invites", json={"email": "bob@globex.com"})

        assert resposta.status_code == 201
        assert len(email.sent) == 1

    def test_e_mail_invalido_e_422(self) -> None:
        client, _ = _app_de_convite()

        assert client.post("/api/v1/invites", json={"email": "nao-e-email"}).status_code == 422

    def test_envio_que_falha_devolve_502_e_revoga_o_convite(self) -> None:
        """Convite criado que ninguém recebeu é pior que convite que não foi criado."""
        client, _ = _app_de_convite()

        class SmtpQuebrado:
            async def send(self, message: object) -> None:
                raise RuntimeError("servidor recusou")

        client.app.state.email = SmtpQuebrado()  # type: ignore[attr-defined]

        resposta = client.post("/api/v1/invites", json={"email": "convidada@exemplo.invalido"})

        assert resposta.status_code == 502
        assert resposta.json()["detail"]["code"] == "invite_not_sent"
        assert client.get("/api/v1/invites").json() == []


class TestListarConvites:
    def test_a_listagem_mostra_o_endereco_de_cada_pendente(self) -> None:
        """É o que torna o painel útil: a pergunta do admin é "já chamei essa pessoa?"."""
        client, _ = _app_de_convite()
        client.post("/api/v1/invites", json={"email": "convidada@exemplo.invalido"})

        pendentes = client.get("/api/v1/invites").json()

        assert [p["email"] for p in pendentes] == ["convidada@exemplo.invalido"]


class TestAceitarConvite:
    def test_a_conta_nasce_com_o_endereco_do_convite(self) -> None:
        client, email = _app_de_convite()
        client.post("/api/v1/invites", json={"email": "convidada@exemplo.invalido"})
        convidada = tls_client(client.app)  # type: ignore[arg-type]

        resposta = convidada.post(
            "/api/v1/invites/accept",
            json={"token": _token_do_email(email), "password": "senha-bem-longa"},
        )

        assert resposta.status_code == 201
        assert resposta.json() == {
            "email": "convidada@exemplo.invalido",
            "role": "member",
        }

    def test_a_previa_diz_de_quem_e_o_convite(self) -> None:
        client, email = _app_de_convite()
        client.post("/api/v1/invites", json={"email": "convidada@exemplo.invalido"})

        corpo = client.post(
            "/api/v1/invites/preview", json={"token": _token_do_email(email)}
        ).json()

        assert corpo["organization_name"] == "Acme"
        assert corpo["email"] == "convidada@exemplo.invalido"


class TestCorpoDoEmail:
    """O texto é produto: a função é pura e testada sozinha."""

    def test_traz_o_link_o_nome_e_o_prazo(self) -> None:
        mensagem = invite_email(
            to="convidada@exemplo.invalido",
            organization_name="Acme",
            link="https://sensus.invalido/convite#um-token",
            expires_at=datetime(2026, 9, 21, 12, 0, tzinfo=UTC),
        )

        assert mensagem.to == "convidada@exemplo.invalido"
        assert "Acme" in mensagem.subject
        assert "https://sensus.invalido/convite#um-token" in mensagem.body
        assert "21/09/2026" in mensagem.body
        # Quem não esperava o convite precisa saber que pode ignorar.
        assert "ignore" in mensagem.body.lower()

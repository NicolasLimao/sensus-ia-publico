"""O token de sessão precisa VENCER.

Antes desta fatia ele era assinado sem prazo e só o cookie tinha `max_age` — quem
copiasse o token entrava para sempre, porque o prazo morava no navegador do usuário,
que é justamente quem não deve ser a autoridade sobre isso.
"""

import time
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from itsdangerous import URLSafeTimedSerializer
from itsdangerous.timed import TimestampSigner

from conftest import cadastrar, tls_client
from sensus_api.config import Settings
from sensus_api.principal import Principal, require_session
from sensus_api.session import SessionToken, read_session, sign_session

SIGNUP = {"organization_name": "Acme", "email": "ana@acme.com"}
SECRET = "segredo-de-teste"
UMA_HORA = 3600


def test_token_recem_assinado_vale() -> None:
    token = sign_session("u1", 0, SECRET)

    assert read_session(token, SECRET, max_age_seconds=UMA_HORA) == SessionToken("u1", 0)


def test_o_token_carrega_o_contador_de_sessao() -> None:
    """É o que permite ao servidor recusar um token de antes do logout."""
    token = sign_session("u1", 7, SECRET)

    lido = read_session(token, SECRET, max_age_seconds=UMA_HORA)
    assert lido is not None
    assert lido.session_epoch == 7


def test_token_vencido_nao_vale(monkeypatch: pytest.MonkeyPatch) -> None:
    """Assina com o relógio duas horas atrás e lê com prazo de uma.

    O carimbo vem do `TimestampSigner`, não do serializer — é ele que o
    `URLSafeTimedSerializer` instancia para assinar.
    """
    monkeypatch.setattr(
        TimestampSigner,
        "get_timestamp",
        lambda self: int(time.time()) - 2 * UMA_HORA,
    )
    token = sign_session("u1", 0, SECRET)
    monkeypatch.undo()

    assert read_session(token, SECRET, max_age_seconds=UMA_HORA) is None


def test_token_de_outro_segredo_nao_vale() -> None:
    token = sign_session("u1", 0, "outro-segredo")

    assert read_session(token, SECRET, max_age_seconds=UMA_HORA) is None


def test_lixo_nao_estoura() -> None:
    assert read_session("nao-e-um-token", SECRET, max_age_seconds=UMA_HORA) is None


def test_token_no_formato_da_fatia_3a_nao_vale() -> None:
    """A 3a assinava a string do id, sem contador. Um token daqueles não pode ser
    aceito: ele não diz de qual geração de sessão é, e seria impossível encerrá-lo.
    O salt novo o derruba já na assinatura, antes de qualquer validação de forma."""
    formato_antigo = URLSafeTimedSerializer(SECRET, salt="sensus-session-v1").dumps("u1")

    assert read_session(formato_antigo, SECRET, max_age_seconds=UMA_HORA) is None


def test_payload_de_forma_errada_nao_vale() -> None:
    """Assinado por nós, mas sem os campos que a sessão precisa — cai pelo mesmo
    caminho do lixo, sem estourar."""
    nosso_salt = URLSafeTimedSerializer(SECRET, salt="sensus-session-v2")

    assert read_session(nosso_salt.dumps("u1"), SECRET, max_age_seconds=UMA_HORA) is None
    assert read_session(nosso_salt.dumps({"uid": "u1"}), SECRET, UMA_HORA) is None
    assert read_session(nosso_salt.dumps({"uid": 1, "epoch": 0}), SECRET, UMA_HORA) is None
    assert read_session(nosso_salt.dumps({"uid": "u1", "epoch": "0"}), SECRET, UMA_HORA) is None
    # `bool` é subclasse de `int` em Python: sem a rejeição explícita, `True` viraria
    # a geração 1 e um token forjado com "epoch": true passaria pela validação de forma.
    assert read_session(nosso_salt.dumps({"uid": "u1", "epoch": True}), SECRET, UMA_HORA) is None


def test_rota_protegida_exige_sessao(app: FastAPI) -> None:
    @app.get("/api/v1/_probe")
    async def probe(
        principal: Annotated[Principal, Depends(require_session)],
    ) -> dict[str, str]:
        return {"user_id": principal.user_id}

    with tls_client(app) as protected_client:
        assert protected_client.get("/api/v1/_probe").status_code == 401

        cadastrar(protected_client, app, **SIGNUP)
        second = protected_client.get("/api/v1/_probe")
        assert second.status_code == 200
        assert second.json()["user_id"]


def test_token_com_contador_velho_nao_entra(
    client: TestClient, app: FastAPI, settings: Settings
) -> None:
    """Um token bem assinado, dentro do prazo, e ainda assim recusado: é o contador
    que decide. Sem esta comparação, o incremento do logout não faria nada."""
    cadastrar(client, app, **SIGNUP)
    account = app.state.users.accounts["ana@acme.com"]

    client.cookies.set(
        "sensus_session",
        sign_session(account.user_id, account.session_epoch + 1, settings.session_secret),
    )

    resposta = client.get("/api/v1/auth/me")
    assert resposta.status_code == 401
    assert resposta.json()["detail"]["code"] == "no_session"

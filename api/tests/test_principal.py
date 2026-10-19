"""O principal é resolvido A CADA requisição, não carregado no cookie.

É isso que faz a autorização não ficar velha: quem for removido da organização ou
rebaixado perde o poder na requisição seguinte, não quando o cookie expirar.
"""

from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import HTTPException, Request, Response

from sensus_api.config import Settings
from sensus_api.principal import Principal, emit_session_cookie, require_admin
from sensus_api.session import SESSION_COOKIE, read_session
from sensus_api.users import Account


def test_admin_passa() -> None:
    principal = Principal(user_id="u1", tenant_id="t1", role="admin")

    assert require_admin(principal) is principal


def test_membro_toma_403() -> None:
    principal = Principal(user_id="u1", tenant_id="t1", role="member")

    with pytest.raises(HTTPException) as erro:
        require_admin(principal)

    assert erro.value.status_code == 403


def test_emitir_cookie_assina_a_geracao_CORRENTE_da_conta(settings: Settings) -> None:
    """Se assinasse um valor fixo — por exemplo `0` —, ninguém que já tivesse saído uma
    vez conseguiria voltar a entrar: o cookie novo divergiria do contador da conta. A
    rota de login cobre isso de ponta a ponta; aqui a função é exercitada direto,
    porque a partir desta fatia ela tem mais de um chamador.
    """
    response = Response()
    # A função só lê `request.app.state.settings`; um stub com esse caminho basta e
    # evita subir aplicação para testar uma função de três linhas.
    request = cast(
        Request,
        SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings))),
    )
    account = Account(
        user_id="u1",
        tenant_id="t1",
        email="ana@acme.com",
        password_hash="hash",
        role="admin",
        session_epoch=3,
    )

    emit_session_cookie(response, account, request)

    cabecalho = response.headers["set-cookie"]
    token = cabecalho.split(f"{SESSION_COOKIE}=", 1)[1].split(";", 1)[0]
    lido = read_session(
        token, settings.session_secret, max_age_seconds=settings.session_max_age_seconds
    )
    assert lido is not None
    assert lido.user_id == "u1"
    assert lido.session_epoch == 3
    assert "HttpOnly" in cabecalho

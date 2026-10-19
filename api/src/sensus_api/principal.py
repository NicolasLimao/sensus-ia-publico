"""Quem está falando com a API, resolvido por requisição."""

from dataclasses import dataclass

from fastapi import HTTPException, Request, Response, status

from sensus_api.session import SESSION_COOKIE, SessionToken, read_session, sign_session
from sensus_api.users import Account


@dataclass(frozen=True)
class Principal:
    user_id: str
    tenant_id: str
    role: str


def session_from_request(request: Request) -> SessionToken | None:
    """O token da requisição, se houver um bem assinado e dentro do prazo.

    Mora aqui, e não em `session.py`, porque ler cookie é assunto de borda HTTP: o
    módulo do token não conhece FastAPI. `require_session` e o logout são os dois
    lugares que precisam disto, e precisam da MESMA resposta.
    """
    settings = request.app.state.settings
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    return read_session(
        token,
        settings.session_secret,
        max_age_seconds=settings.session_max_age_seconds,
    )


def emit_session_cookie(response: Response, account: Account, request: Request) -> None:
    """A operação simétrica de `session_from_request`, e mora aqui pelo mesmo motivo:
    cookie é assunto de borda HTTP, e o módulo do token não conhece FastAPI.

    Três rotas a chamam — cadastro, login e aceite de convite —, e as três precisam
    assinar a geração CORRENTE da conta.
    """
    settings = request.app.state.settings
    response.set_cookie(
        SESSION_COOKIE,
        sign_session(account.user_id, account.session_epoch, settings.session_secret),
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
        max_age=settings.session_max_age_seconds,
        path="/",
    )


async def require_session(request: Request) -> Principal:
    """Cookie → usuário → organização e papel, ou 401.

    A consulta por requisição é o preço de a autorização não ficar velha. É indexada
    e barata; um papel obsoleto no cookie não teria conserto até ele expirar.

    A geração do token é comparada com a da conta: o logout incrementa a da conta, e
    todo token emitido antes passa a divergir. A resposta é a MESMA de sessão ausente
    ou vencida — distinguir "encerrada" de "não existe" entregaria quais ids têm conta.
    """
    session = session_from_request(request)
    account = (
        await request.app.state.users.find_by_id(session.user_id) if session is not None else None
    )
    if session is None or account is None or account.session_epoch != session.session_epoch:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "no_session", "message": "Sessão ausente ou inválida."},
        )
    return Principal(user_id=account.user_id, tenant_id=account.tenant_id, role=account.role)


def require_admin(principal: Principal) -> Principal:
    if principal.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "forbidden_role",
                "message": "Só quem administra a organização pode alterar documentos.",
            },
        )
    return principal

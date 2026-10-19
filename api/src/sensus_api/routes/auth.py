"""Login, logout e identidade da sessão.

O cadastro saiu daqui na fatia 3f: ele virou um pedido em dois passos, com
vocabulário próprio (pendência, carimbos, veredito), e mora em
`routes/signup_confirmation.py` — junto da confirmação que o completa."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr

from sensus_api.passwords import burn_time, verify_password
from sensus_api.principal import (
    Principal,
    emit_session_cookie,
    require_session,
    session_from_request,
)
from sensus_api.session import SESSION_COOKIE

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth")

# Mesma resposta para e-mail inexistente e senha errada. Distinguir os dois entregaria
# quais e-mails têm conta.
_CREDENCIAIS = {"code": "invalid_credentials", "message": "E-mail ou senha inválidos."}


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class AccountResponse(BaseModel):
    email: str
    role: str


class MeResponse(BaseModel):
    email: str
    role: str
    suggestions: list[str]


@router.post("/login")
async def login(payload: LoginRequest, request: Request, response: Response) -> AccountResponse:
    account = await request.app.state.users.find_by_email(payload.email)
    if account is None:
        # Sem isto, o e-mail inexistente responde mais rápido que a senha errada, e o
        # tempo de resposta entrega o que o corpo da resposta esconde.
        burn_time()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_CREDENCIAIS)

    if not verify_password(payload.password, account.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_CREDENCIAIS)

    emit_session_cookie(response, account, request)
    return AccountResponse(email=account.email, role=account.role)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response) -> None:
    """Encerra a sessão dos dois lados: o cookie no navegador e a geração no servidor.

    Sem `Depends(require_session)` de propósito — sair é idempotente e responde 204
    mesmo sem sessão válida. Um token de geração já vencida também incrementa: não
    fecha nada de novo — quem o carrega já tinha a sessão — mas também não é
    inofensivo de graça: um token vazado e ainda assinado deixa quem o tem chamar
    `logout` repetidas vezes e derrubar o dono de todo dispositivo, até o token
    expirar. Fechar isso custaria uma consulta e um ramo a mais que a spec (§3)
    decidiu não pagar: quem já tinha o token já tinha a sessão, e `SameSite=Lax`
    barra o disparo entre sites.

    O apagamento do cookie vem DEPOIS do incremento, mas não depende dele: se o
    `bump_session_epoch` falhar, o cookie ainda assim é apagado e a resposta ainda
    assim é 204 — a tabela do §3 da spec promete "204 sempre", e o botão "Sair" não
    tem outro caminho a seguir. O preço é que a sessão pode NÃO ter sido encerrada no
    servidor: o cookie some deste navegador, mas uma cópia do token continua valendo
    até o prazo, e só o log denuncia o quê.
    """
    session = session_from_request(request)
    if session is not None:
        try:
            await request.app.state.users.bump_session_epoch(session.user_id)
        except Exception:
            logger.exception(
                "falha ao incrementar session_epoch no logout: user_id=%s — o cookie "
                "deste navegador foi apagado, mas a sessão NÃO foi encerrada no "
                "servidor; uma cópia do token continua válida até o prazo",
                session.user_id,
            )
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/me")
async def me(
    request: Request, principal: Annotated[Principal, Depends(require_session)]
) -> MeResponse:
    account = await request.app.state.users.find_by_id(principal.user_id)
    assert account is not None  # require_session acabou de resolvê-lo
    # As sugestões vêm da config do TENANT desde a fatia 4a (do ambiente, quando ele
    # nunca configurou). É a única parte da config que alcança `member`: ele não pode
    # ver nem editar nada em `/ai-config`, mas é a tela vazia dele que as mostra — e
    # sugestão boa é específica da base, que é do cliente.
    config = await request.app.state.ai_config_resolver.resolve(principal.tenant_id)
    return MeResponse(
        email=account.email,
        role=account.role,
        suggestions=list(config.suggestions),
    )

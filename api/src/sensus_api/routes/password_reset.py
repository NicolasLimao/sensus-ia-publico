"""Reset de senha: pedir o link, conferir o link, trocar a senha.

Módulo próprio, e não `auth.py`, pelo mesmo critério que separou os convites: são três
rotas com vocabulário próprio (carimbos, veredito, e-mail) e `auth.py` não vira o
arquivo que faz tudo. O prefixo continua `/api/v1/auth`, porque é lá que o contrato
pertence.

As três são ANÔNIMAS: quem esqueceu a senha não consegue provar quem é — é justamente
esse o problema que elas resolvem.
"""

import hashlib
import logging
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field

from sensus_api.email import EmailMessage, EmailSender
from sensus_api.password_resets import ResetStamps
from sensus_api.passwords import hash_password
from sensus_api.principal import emit_session_cookie

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth")

# Mesmos 32 bytes do convite: o link É a credencial, e a entropia é a defesa inteira.
_TOKEN_BYTES = 32


def _token_hash(token: str) -> str:
    """sha256, não argon2 — a mesma decisão do convite: argon2 existe para segredo de
    baixa entropia escolhido por gente, e aqui são 256 bits sorteados por nós."""
    return hashlib.sha256(token.encode()).hexdigest()


# A frase é parte do contrato, não decoração: é a MESMA para conta existente, conta
# inexistente e pedido freado, e é o que impede a tela de desfazer o que o 202 protege.
_ENVIADO_MENSAGEM = "Se houver conta com esse e-mail, o link está a caminho."

_MENSAGENS = {
    "reset_not_found": "Este link não existe.",
    "reset_used": "Este link já foi usado. Faça login com a senha nova.",
    "reset_superseded": "Este link foi substituído. Abra o e-mail mais recente.",
    "reset_expired": "Este link venceu. Peça outro.",
}


def verdict_for(stamps: ResetStamps | None, *, now: datetime) -> tuple[int, str] | None:
    """O veredito derivado dos carimbos: `None` quer dizer **pendente**.

    Pura, e usada pela prévia e pela troca — dois lugares derivando o estado dariam
    duas verdades sobre o mesmo link, que é o que a ausência de coluna `status` evita
    no banco.

    A precedência é deliberada, e cada degrau muda o que a tela manda fazer: **usado**
    (410) manda fazer login, porque a senha já é outra; **substituído** (410) manda
    abrir o e-mail mais recente, porque há sucessor vivo; **vencido** (410) manda pedir
    outro. Trocar a ordem faria a tela dar o conselho errado — dizer "venceu" a um link
    já usado mandaria pedir um link que não é necessário.

    É irmã da `verdict_for` dos convites, e duplicada de propósito: os códigos e a
    precedência são outros, e generalizar as duas com dois dicionários esconderia
    justamente a precedência, que é a parte que carrega decisão.
    """
    if stamps is None:
        return status.HTTP_404_NOT_FOUND, "reset_not_found"
    if stamps.used_at is not None:
        return status.HTTP_410_GONE, "reset_used"
    if stamps.superseded_at is not None:
        return status.HTTP_410_GONE, "reset_superseded"
    if stamps.expires_at <= now:
        return status.HTTP_410_GONE, "reset_expired"
    return None


def _reset_error(verdict: tuple[int, str]) -> HTTPException:
    http_status, code = verdict
    return HTTPException(
        status_code=http_status, detail={"code": code, "message": _MENSAGENS[code]}
    )


def reset_email(*, to: str, link: str, expires_at: datetime) -> EmailMessage:
    """O corpo do e-mail. Puro, e testado sozinho: é texto de produto, muda com
    frequência e não precisa de SMTP para ser verificado.

    Diz o prazo porque um link que morre calado vira suporte; e diz o que fazer se a
    pessoa não pediu, que é o único aviso que ela recebe de um reset alheio enquanto
    não houver e-mail de "sua senha mudou".
    """
    return EmailMessage(
        to=to,
        subject="Redefinir sua senha no Sensus",
        body=(
            "Alguém pediu para redefinir a senha desta conta.\n\n"
            f"Abra este link para escolher uma senha nova:\n{link}\n\n"
            f"O link vale até {expires_at.strftime('%d/%m/%Y às %H:%M UTC')} e só pode "
            "ser usado uma vez.\n\n"
            "Se não foi você, ignore este e-mail: nada muda enquanto o link não for "
            "aberto."
        ),
    )


class ForgotRequest(BaseModel):
    email: EmailStr


class TokenRequest(BaseModel):
    # No CORPO, nunca na query: fora do log de acesso da API, dos proxies do caminho e
    # do `Referer`. `max_length` porque `token_urlsafe(32)` dá 43 caracteres.
    token: str = Field(min_length=1, max_length=200)


class ResetRequestBody(TokenRequest):
    # O mesmo mínimo do cadastro e do aceite de convite: recuperar a conta não pode ser
    # a porta de entrada da senha fraca.
    password: str = Field(min_length=12, max_length=200)


class SentResponse(BaseModel):
    status: str
    message: str


class ResetPreviewResponse(BaseModel):
    email: str


class AccountResponse(BaseModel):
    email: str
    role: str


async def _enviar(sender: EmailSender, message: EmailMessage) -> None:
    """A resposta já saiu; só o log sobra. Engolir a exceção é deliberado: deixá-la
    subir numa background task só produziria um traceback sem ninguém para lê-lo, e
    nada no navegador — este log é o registro."""
    try:
        await sender.send(message)
    except Exception:
        logger.exception(
            "falha ao enviar e-mail de reset para %s — a pessoa não recebeu o link e "
            "precisará pedir outro",
            message.to,
        )


@router.post("/forgot-password", status_code=status.HTTP_202_ACCEPTED)
async def forgot_password(
    payload: ForgotRequest, request: Request, background: BackgroundTasks
) -> SentResponse:
    """202 sempre, com o mesmo corpo — conta existente, conta inexistente ou pedido
    freado.

    O e-mail sai em `BackgroundTasks`, DEPOIS da resposta, e isso não é otimização: é o
    que torna o 202 honesto também no relógio. Síncrono, a resposta demoraria o que o
    SMTP demorasse, e o tempo passaria a distinguir e-mail com conta de e-mail sem
    conta — o vazamento que o corpo fixo existe para evitar. É o mesmo problema que o
    `burn_time()` do login resolve de outro jeito; aqui sai de graça.

    O preço, registrado na spec (§9): um processo que caia entre a resposta e o envio
    perde o e-mail em silêncio, e a pessoa pede outro link.
    """
    settings = request.app.state.settings
    token = secrets.token_urlsafe(_TOKEN_BYTES)
    expires_at = datetime.now(UTC) + timedelta(seconds=settings.password_reset_max_age_seconds)
    pedido = await request.app.state.password_resets.request(
        email=payload.email,
        token_hash=_token_hash(token),
        expires_at=expires_at,
        throttle_seconds=settings.password_reset_throttle_seconds,
    )
    if pedido is not None:
        # O destinatário é o e-mail do BANCO, não o que veio no corpo: eles só diferem
        # na caixa, mas mandar de volta a entrada do chamador é o tipo de eco que vira
        # vetor quando alguém encontra um jeito de forjá-la.
        mensagem = reset_email(
            to=pedido.email,
            link=f"{settings.public_base_url}/redefinir#{token}",
            expires_at=expires_at,
        )
        background.add_task(_enviar, request.app.state.email, mensagem)
    return SentResponse(status="sent", message=_ENVIADO_MENSAGEM)


@router.post("/reset-password/preview")
async def preview_reset(payload: TokenRequest, request: Request) -> ResetPreviewResponse:
    """Existe para a tela não mentir por omissão: sem ela, a pessoa digita a senha nova
    e só então descobre que o link venceu.

    Devolver o e-mail do dono não revela nada novo — quem apresenta o token já recebeu
    o e-mail que o continha. É a mesma lógica da prévia do convite.
    """
    stamps = await request.app.state.password_resets.stamps_by_token_hash(
        _token_hash(payload.token)
    )
    verdict = verdict_for(stamps, now=datetime.now(UTC))
    if verdict is not None:
        raise _reset_error(verdict)
    assert stamps is not None  # `verdict_for` devolve 404 quando não há linha
    return ResetPreviewResponse(email=stamps.email)


@router.post("/reset-password")
async def reset_password(
    payload: ResetRequestBody, request: Request, response: Response
) -> AccountResponse:
    """Troca a senha, derruba todas as sessões e já emite uma nova.

    A conta que volta do `consume` traz a geração NOVA, e é com ela que o cookie é
    assinado: emitir com a geração antiga produziria uma sessão que o próprio
    incremento acabou de invalidar — a pessoa redefiniria a senha e cairia na tela de
    login.
    """
    resets = request.app.state.password_resets
    token_hash = _token_hash(payload.token)
    account = await resets.consume(
        token_hash=token_hash, password_hash=hash_password(payload.password)
    )
    if account is None:
        # Só agora vale a consulta a mais: ela existe para dizer POR QUE, e só o
        # caminho que já falhou a paga.
        stamps = await resets.stamps_by_token_hash(token_hash)
        verdict = verdict_for(stamps, now=datetime.now(UTC))
        # Carimbos que parecem pendentes depois de um consumo que não consumiu nada só
        # acontecem numa corrida: outra transação ganhou o `update` e o commit dela
        # ainda não estava visível nesta leitura. O link foi usado — por ela.
        raise _reset_error(verdict or (status.HTTP_410_GONE, "reset_used"))

    emit_session_cookie(response, account, request)
    return AccountResponse(email=account.email, role=account.role)

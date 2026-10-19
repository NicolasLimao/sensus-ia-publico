"""Cadastro em dois passos: pedir o link, conferir o link, criar a organização.

Módulo próprio, e não `auth.py`, pelo mesmo critério que separou convites e reset: são
três rotas com vocabulário próprio (pendência, carimbos, veredito) e `auth.py` não vira
o arquivo que faz tudo. O prefixo continua `/api/v1/auth`, porque é lá que o contrato
pertence.

As três são ANÔNIMAS: quem se cadastra ainda não tem conta — é esse o ponto.
"""

import hashlib
import logging
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import (
    APIRouter,
    BackgroundTasks,
    HTTPException,
    Request,
    Response,
    status,
)
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from sensus_api.email import EmailMessage, EmailSender
from sensus_api.passwords import hash_password
from sensus_api.pending_signups import SignupStamps
from sensus_api.principal import emit_session_cookie
from sensus_api.users import EmailAlreadyUsed

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth")

# Os mesmos 32 bytes do convite e do reset: o link É a credencial, e a entropia é a
# defesa inteira.
_TOKEN_BYTES = 32


def _token_hash(token: str) -> str:
    """sha256, não argon2 — a mesma decisão das outras duas fatias: argon2 existe para
    segredo de baixa entropia escolhido por gente, e aqui são 256 bits sorteados."""
    return hashlib.sha256(token.encode()).hexdigest()


# A frase é parte do contrato, não decoração: é a MESMA para endereço livre, endereço
# com conta e pedido freado, e é o que impede a tela de desfazer o que o 202 protege.
_ENVIADO_MENSAGEM = "Se o endereço estiver livre, o link de confirmação está a caminho."

_MENSAGENS = {
    "signup_not_found": "Este link não existe.",
    "signup_used": "Este cadastro já foi concluído. Faça login.",
    "signup_superseded": "Este link foi substituído. Abra o e-mail mais recente.",
    "signup_expired": "Este link venceu. Comece o cadastro de novo.",
}


def verdict_for(stamps: SignupStamps | None, *, now: datetime) -> tuple[int, str] | None:
    """O veredito derivado dos carimbos: `None` quer dizer **pendente**.

    Pura, e usada pela prévia e pela confirmação — dois lugares derivando o estado
    dariam duas verdades sobre o mesmo link, que é o que a ausência de coluna `status`
    evita no banco.

    Irmã das `verdict_for` do convite e do reset, e duplicada de propósito: os códigos e
    a precedência são outros, e generalizar as três esconderia justamente a precedência,
    que é a parte que carrega decisão. **Usado** manda fazer login, porque a conta já
    existe; **substituído** manda abrir o e-mail mais recente, porque há sucessor vivo;
    **vencido** manda recomeçar.
    """
    if stamps is None:
        return status.HTTP_404_NOT_FOUND, "signup_not_found"
    if stamps.consumed_at is not None:
        return status.HTTP_410_GONE, "signup_used"
    if stamps.superseded_at is not None:
        return status.HTTP_410_GONE, "signup_superseded"
    if stamps.expires_at <= now:
        return status.HTTP_410_GONE, "signup_expired"
    return None


def _signup_error(verdict: tuple[int, str]) -> HTTPException:
    http_status, code = verdict
    return HTTPException(
        status_code=http_status, detail={"code": code, "message": _MENSAGENS[code]}
    )


def confirmation_email(*, to: str, link: str, expires_at: datetime) -> EmailMessage:
    """O e-mail de quem pediu o cadastro. Puro, e testado sozinho: é texto de produto.

    **Não recebe o nome da organização**, e a assinatura é a garantia: o nome é texto
    escolhido por quem digitou o formulário, e este e-mail pode chegar à caixa de outra
    pessoa. Ele aparece na tela de confirmação, depois do clique, onde já é a nossa
    página falando.

    Diz o prazo porque um link que morre calado vira suporte, e diz que nada é criado
    antes do clique — frase que é verdadeira aqui e não era no reset.
    """
    return EmailMessage(
        to=to,
        subject="Confirme seu e-mail no Sensus",
        body=(
            "Alguém começou um cadastro no Sensus com este endereço.\n\n"
            f"Abra este link para escolher sua senha e concluir:\n{link}\n\n"
            f"O link vale até {expires_at.strftime('%d/%m/%Y às %H:%M UTC')} e só pode "
            "ser usado uma vez.\n\n"
            "Se não foi você, ignore este e-mail: nenhuma conta é criada enquanto o "
            "link não for aberto."
        ),
    )


def already_registered_email(*, to: str) -> EmailMessage:
    """O e-mail de quem tentou cadastrar um endereço que já tem conta.

    **Sem link nenhum**: embutir um link de reset transformaria uma rota anônima em
    disparador de redefinição para endereços alheios. E sem nada que o remetente do
    pedido tenha escrito — nem o nome da organização que ele digitou.

    É o que tira do beco sem saída quem esqueceu que já se cadastrou: a tela não pode
    contar, porque o 202 é idêntico, e sem este e-mail ela esperaria para sempre.
    """
    return EmailMessage(
        to=to,
        subject="Este endereço já tem conta no Sensus",
        body=(
            "Alguém tentou criar uma conta no Sensus com este endereço, mas ele já "
            "tem uma.\n\n"
            "Entre com sua senha na tela de entrada. Se você não lembra a senha, use "
            "a opção 'Esqueci minha senha'.\n\n"
            "Se não foi você quem tentou, ignore este e-mail: nada mudou na sua conta."
        ),
    )


class SignupRequest(BaseModel):
    # `extra="forbid"` de propósito: a senha saiu do cadastro, e um cliente antigo que
    # continuasse mandando-a pareceria funcionar enquanto a senha ia para o lixo.
    model_config = ConfigDict(extra="forbid")

    organization_name: str = Field(min_length=1, max_length=120)
    email: EmailStr


class SentResponse(BaseModel):
    status: str
    message: str


class TokenRequest(BaseModel):
    # No CORPO, nunca na query: fora do log de acesso da API, dos proxies do caminho e
    # do `Referer`. `max_length` porque `token_urlsafe(32)` dá 43 caracteres.
    token: str = Field(min_length=1, max_length=200)


class ConfirmRequest(TokenRequest):
    # O mesmo mínimo do reset e do aceite de convite: a porta de entrada do produto não
    # pode ser a porta de entrada da senha fraca.
    password: str = Field(min_length=12, max_length=200)


class SignupPreviewResponse(BaseModel):
    organization_name: str
    email: str


class AccountResponse(BaseModel):
    email: str
    role: str


@router.post("/confirm-signup/preview")
async def preview_signup(payload: TokenRequest, request: Request) -> SignupPreviewResponse:
    """Existe para a tela não mentir por omissão: sem ela, a pessoa digita a senha e só
    então descobre que o link venceu.

    Devolver o endereço não revela nada novo — quem apresenta o token já recebeu o
    e-mail que o continha. E é aqui, e só aqui, que o nome da organização aparece: no
    e-mail ele seria texto de terceiro na caixa de outra pessoa.
    """
    stamps = await request.app.state.pending_signups.stamps_by_token_hash(
        _token_hash(payload.token)
    )
    verdict = verdict_for(stamps, now=datetime.now(UTC))
    if verdict is not None:
        raise _signup_error(verdict)
    assert stamps is not None  # `verdict_for` devolve 404 quando não há linha
    return SignupPreviewResponse(organization_name=stamps.organization_name, email=stamps.email)


@router.post("/confirm-signup", status_code=status.HTTP_201_CREATED)
async def confirm_signup(
    payload: ConfirmRequest, request: Request, response: Response
) -> AccountResponse:
    """Cria a organização e o admin, e já emite a sessão — a pessoa não passa pela tela
    de login depois de confirmar.

    O 409 aqui é legítimo, ao contrário do que o cadastro fazia: quem apresenta o token
    é o dono da caixa de entrada, e o fato é sobre o próprio endereço dele.
    """
    pendings = request.app.state.pending_signups
    token_hash = _token_hash(payload.token)
    try:
        account = await pendings.confirm(
            token_hash=token_hash, password_hash=hash_password(payload.password)
        )
    except EmailAlreadyUsed as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "email_taken", "message": "Este e-mail já tem conta."},
        ) from exc

    if account is None:
        # Só agora vale a consulta a mais: ela existe para dizer POR QUE, e só o caminho
        # que já falhou a paga.
        stamps = await pendings.stamps_by_token_hash(token_hash)
        verdict = verdict_for(stamps, now=datetime.now(UTC))
        # Carimbos que parecem pendentes depois de um consumo que não consumiu nada só
        # acontecem numa corrida: outra transação ganhou o `update` e o commit dela
        # ainda não estava visível nesta leitura. O link foi usado — por ela.
        raise _signup_error(verdict or (status.HTTP_410_GONE, "signup_used"))

    emit_session_cookie(response, account, request)
    return AccountResponse(email=account.email, role=account.role)


async def _enviar(sender: EmailSender, message: EmailMessage) -> None:
    """A resposta já saiu; só o log sobra. Engolir a exceção é deliberado: deixá-la
    subir numa background task só produziria um traceback sem ninguém para lê-lo, e
    nada no navegador — este log é o registro."""
    try:
        await sender.send(message)
    except Exception:
        logger.exception(
            "falha ao enviar e-mail de cadastro para %s — a pessoa não recebeu o link "
            "e precisará começar de novo",
            message.to,
        )


@router.post("/signup", status_code=status.HTTP_202_ACCEPTED)
async def signup(
    payload: SignupRequest, request: Request, background: BackgroundTasks
) -> SentResponse:
    """202 sempre, com o mesmo corpo — endereço livre, endereço com conta ou pedido
    freado. **Nenhuma conta é criada aqui**: quem cria é a confirmação.

    Até a 3f esta rota respondia 409 `email_taken` e entregava, a qualquer um, quais
    endereços têm conta no Sensus. O login já não entregava — nem no corpo, nem no
    status, nem no tempo — e o reset também não; este era o último lugar aberto.

    O e-mail sai em `BackgroundTasks`, DEPOIS da resposta, e isso não é otimização: é o
    que torna o 202 honesto também no relógio. Síncrono, a resposta demoraria o que o
    SMTP demorasse, e o tempo passaria a distinguir endereço com conta de endereço sem
    conta — o vazamento que o corpo fixo existe para evitar.

    Qual dos dois e-mails sai é a única coisa que o `already_has_account` decide, e ele
    não atravessa a borda.
    """
    settings = request.app.state.settings
    token = secrets.token_urlsafe(_TOKEN_BYTES)
    expires_at = datetime.now(UTC) + timedelta(seconds=settings.signup_confirmation_max_age_seconds)
    pedido = await request.app.state.pending_signups.request(
        organization_name=payload.organization_name,
        email=payload.email,
        token_hash=_token_hash(token),
        expires_at=expires_at,
        throttle_seconds=settings.signup_throttle_seconds,
    )
    if pedido is not None:
        # O destinatário é o e-mail normalizado que voltou do diretório, não o que veio
        # no corpo: eles só diferem na caixa, mas ecoar a entrada do chamador é o tipo
        # de eco que vira vetor quando alguém encontra um jeito de forjá-la.
        mensagem = (
            already_registered_email(to=pedido.email)
            if pedido.already_has_account
            else confirmation_email(
                to=pedido.email,
                link=f"{settings.public_base_url}/confirmar#{token}",
                expires_at=expires_at,
            )
        )
        background.add_task(_enviar, request.app.state.email, mensagem)
    return SentResponse(status="sent", message=_ENVIADO_MENSAGEM)

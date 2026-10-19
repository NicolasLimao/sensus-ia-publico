"""Convites: o admin informa um endereço, e quem abre o link do e-mail entra como
`member`.

Módulo próprio, e não `auth.py`: nada disto é cadastro. O papel é checado como em
`documents.py` — `require_admin(principal)` dentro do handler, e não como `Depends` —
porque duas das cinco rotas são ANÔNIMAS de propósito: quem foi convidado ainda não
tem conta.

Desde a 3e o token não aparece em resposta HTTP nenhuma: ele existe só dentro da caixa
de entrada do endereço convidado, e é isso — não a coluna `email` — que faz o convite
provar posse. Quem intercepta um link vê o endereço na própria tela de aceite e o
usaria; o vínculo sozinho não provaria nada.
"""

import hashlib
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field

from sensus_api.email import EmailMessage
from sensus_api.invitations import InvitationStamps
from sensus_api.passwords import hash_password
from sensus_api.principal import (
    Principal,
    emit_session_cookie,
    require_admin,
    require_session,
)
from sensus_api.users import EmailAlreadyUsed

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/invites")

# 32 bytes de aleatoriedade: o link É a credencial, então a entropia é a defesa. Não há
# throttle no token, e não precisa haver — não há o que adivinhar.
_TOKEN_BYTES = 32


def _token_hash(token: str) -> str:
    """sha256 do token, em hexadecimal.

    Não é argon2, e isto é decisão fechada: argon2 existe para segredo de baixa
    entropia escolhido por gente, onde o custo por tentativa é a defesa. Este são 256
    bits gerados por nós — não há dicionário a percorrer, e argon2 só cobraria ~100 ms
    por requisição.
    """
    return hashlib.sha256(token.encode()).hexdigest()


# As frases que o convidado lê. Distinguir os motivos não vaza nada — quem tem um token
# de 256 bits já tem o segredo, e quem não tem recebe 404 em tudo — e é o que permite à
# tela dizer o que fazer em seguida: pedir outro link, ou fazer login.
_MENSAGENS = {
    "invite_not_found": "Este convite não existe.",
    "invite_used": "Este convite já foi usado.",
    "invite_expired": "Este convite venceu.",
    "invite_revoked": "Este convite foi revogado.",
}


def verdict_for(stamps: InvitationStamps | None, *, now: datetime) -> tuple[int, str] | None:
    """O veredito derivado dos carimbos: `None` quer dizer **pendente**.

    Função pura, e separada das rotas, porque a prévia e o aceite precisam da MESMA
    resposta — dois lugares derivando o estado dariam duas verdades sobre o mesmo
    convite, que é exatamente o que a ausência de coluna `status` evita no banco.

    A precedência é deliberada: aceito vence revogado e vencido. Um convite aceito já
    virou uma pessoa na organização, e responder "venceu" a mandaria pedir outro link
    em vez de fazer login.
    """
    if stamps is None:
        return status.HTTP_404_NOT_FOUND, "invite_not_found"
    if stamps.accepted_at is not None:
        return status.HTTP_410_GONE, "invite_used"
    if stamps.revoked_at is not None:
        return status.HTTP_410_GONE, "invite_revoked"
    if stamps.expires_at <= now:
        return status.HTTP_410_GONE, "invite_expired"
    return None


def _invite_error(verdict: tuple[int, str]) -> HTTPException:
    http_status, code = verdict
    return HTTPException(
        status_code=http_status, detail={"code": code, "message": _MENSAGENS[code]}
    )


def invite_email(
    *, to: str, organization_name: str, link: str, expires_at: datetime
) -> EmailMessage:
    """O corpo do convite. Puro, e testado sozinho: é texto de produto.

    Não diz QUEM convidou: exigiria juntar `created_by` com `users` para pegar o
    endereço do admin, e o nome da organização já responde "por que recebi isto?". Se
    virar pedido real, é uma coluna a mais no `join`, não um redesenho.
    """
    return EmailMessage(
        to=to,
        subject=f"Você foi convidado para {organization_name} no Sensus",
        body=(
            f"Você foi convidado para entrar em {organization_name} no Sensus.\n\n"
            f"Abra este link para criar sua senha e entrar:\n{link}\n\n"
            f"O link vale até {expires_at.strftime('%d/%m/%Y às %H:%M UTC')} e só pode "
            "ser usado uma vez.\n\n"
            "Se você não esperava este convite, ignore este e-mail."
        ),
    )


class TokenRequest(BaseModel):
    # O token vai no CORPO, e não na query: fora do log de acesso da API e dos proxies
    # do caminho. `max_length` porque `token_urlsafe(32)` dá 43 caracteres — o resto é
    # entrada inútil que não precisa chegar ao sha256.
    token: str = Field(min_length=1, max_length=200)


class AcceptRequest(TokenRequest):
    # Sem campo de e-mail desde a 3e, e isso não é validação — é estrutura: não existe
    # mais entrada por onde outro endereço entraria.
    #
    # O mesmo mínimo do cadastro: entrar por convite não pode ser a porta de senha
    # fraca.
    password: str = Field(min_length=12, max_length=200)


class CreateInviteRequest(BaseModel):
    email: EmailStr


class InvitePreviewResponse(BaseModel):
    organization_name: str
    # Não revela nada novo: quem apresenta o token o recebeu naquela caixa. É o que
    # permite à tela dizer "você foi convidado como fulano@x" em vez de pedir fé.
    email: str
    expires_at: str


class AcceptedResponse(BaseModel):
    email: str
    role: str


class CreatedInviteResponse(BaseModel):
    id: str
    # Para quem foi — e nada mais. O token NÃO está aqui desde a 3e: um campo devolvido
    # por engano desfaria a fatia inteira em silêncio.
    email: str
    expires_at: str


class PendingInviteResponse(BaseModel):
    id: str
    email: str
    created_at: str
    expires_at: str


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_invite(
    payload: CreateInviteRequest,
    request: Request,
    principal: Annotated[Principal, Depends(require_session)],
) -> CreatedInviteResponse:
    """Cria o convite e **manda o link por e-mail**. A resposta não traz o token.

    Desde a 3e o token existe só dentro da caixa de entrada — é isso, e não a coluna
    `email`, que faz o convite provar posse do endereço: quem intercepta um link vê o
    endereço na tela de aceite e o usaria, então o vínculo sozinho não provaria nada.

    O envio é SÍNCRONO, ao contrário do reset de senha. Lá, o `BackgroundTasks` existe
    porque o tempo de resposta seria um oráculo de quais e-mails têm conta. Aqui não há
    oráculo — o admin está autenticado e foi ele quem digitou o endereço — e ele precisa
    saber se o convite saiu.
    """
    require_admin(principal)
    settings = request.app.state.settings

    # `find_by_email` é GLOBAL: ela atravessa tenants, porque o login precisa disso.
    # Responder 409 sempre que ela encontrar alguém transformaria este painel num
    # oráculo de enumeração entre tenants — o admin da Acme descobriria quem tem conta
    # na Globex só tentando convidá-lo. Por isso a comparação de tenant: conta em outra
    # organização segue o caminho normal, e a colisão aparece só no aceite.
    existente = await request.app.state.users.find_by_email(payload.email)
    if existente is not None and existente.tenant_id == principal.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "already_member",
                "message": "Essa pessoa já faz parte da organização.",
            },
        )

    token = secrets.token_urlsafe(_TOKEN_BYTES)
    invitation = await request.app.state.invitations.create(
        tenant_id=principal.tenant_id,
        created_by=principal.user_id,
        email=payload.email,
        token_hash=_token_hash(token),
        expires_at=datetime.now(UTC) + timedelta(seconds=settings.invite_max_age_seconds),
    )

    # Uma consulta a mais, e ela é o preço de o e-mail nomear a organização: o nome vive
    # em `organizations`, e o único caminho que esta borda tem até ele é a fresta de
    # leitura — a mesma que a prévia usa. A alternativa seria `Invitation` carregar o
    # nome do tenant, o que poria um dado da organização dentro do convite só para
    # evitar um `select`.
    organizacao = await request.app.state.invitations.stamps_by_token_hash(_token_hash(token))
    assert organizacao is not None  # acabou de ser criado
    try:
        await request.app.state.email.send(
            invite_email(
                to=invitation.email,
                organization_name=organizacao.organization_name,
                link=f"{settings.public_base_url}/convite#{token}",
                expires_at=invitation.expires_at,
            )
        )
    except Exception as exc:
        # Revogar, e não apagar: o `insert` já comitou, e revogar deixa rastro de que
        # houve tentativa. Apagar faria o convite que ninguém recebeu sumir também do
        # histórico.
        logger.exception("falha ao enviar convite para %s", invitation.email)
        await request.app.state.invitations.revoke(
            tenant_id=principal.tenant_id, invitation_id=invitation.id
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "invite_not_sent",
                "message": (
                    "Não foi possível enviar o convite. Ninguém recebeu nada — tente de novo."
                ),
            },
        ) from exc

    return CreatedInviteResponse(
        id=invitation.id,
        email=invitation.email,
        expires_at=invitation.expires_at.isoformat(),
    )


@router.get("")
async def list_invites(
    request: Request, principal: Annotated[Principal, Depends(require_session)]
) -> list[PendingInviteResponse]:
    """Só os pendentes. Convite usado é uma pessoa na organização, e é lá que ela vai
    aparecer quando existir tela de pessoas; convite vencido é ruído."""
    require_admin(principal)
    invitations = await request.app.state.invitations.list_pending(tenant_id=principal.tenant_id)
    return [
        PendingInviteResponse(
            id=invitation.id,
            email=invitation.email,
            created_at=invitation.created_at.isoformat(),
            expires_at=invitation.expires_at.isoformat(),
        )
        for invitation in invitations
    ]


@router.delete("/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invite(
    invitation_id: UUID,
    request: Request,
    principal: Annotated[Principal, Depends(require_session)],
) -> Response:
    """204 sempre: revogar o que não existe, o que é de outra organização ou o que já
    foi aceito não é erro. Um 404 aqui entregaria quais ids de convite existem.

    `UUID` no path, e não `str`: sem o tipo, uma string qualquer chegaria ao Postgres e
    o `22P02` viraria 500.
    """
    require_admin(principal)
    await request.app.state.invitations.revoke(
        tenant_id=principal.tenant_id, invitation_id=str(invitation_id)
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/preview")
async def preview_invite(payload: TokenRequest, request: Request) -> InvitePreviewResponse:
    """Anônima: quem foi convidado ainda não tem conta. Passa pela fresta `security
    definer` da 006, porque não há tenant a informar."""
    stamps = await request.app.state.invitations.stamps_by_token_hash(_token_hash(payload.token))
    verdict = verdict_for(stamps, now=datetime.now(UTC))
    if verdict is not None:
        raise _invite_error(verdict)
    assert stamps is not None  # `verdict_for` devolve 404 quando não há linha
    return InvitePreviewResponse(
        organization_name=stamps.organization_name,
        email=stamps.email,
        expires_at=stamps.expires_at.isoformat(),
    )


@router.post("/accept", status_code=status.HTTP_201_CREATED)
async def accept_invite(
    payload: AcceptRequest, request: Request, response: Response
) -> AcceptedResponse:
    """Consome o convite, cria o `member` e emite sessão.

    Uma sessão já aberta neste navegador é ignorada de propósito: um navegador, uma
    sessão, e o cookie novo substitui o anterior. Quem aceitou um convite quer entrar
    como a conta nova.
    """
    invitations = request.app.state.invitations
    token_hash = _token_hash(payload.token)
    try:
        account = await invitations.accept(
            token_hash=token_hash, password_hash=hash_password(payload.password)
        )
    except EmailAlreadyUsed as exc:
        # Limitação conhecida, e desde a 3e ela sobrou só para conta de OUTRA
        # organização: na própria, a criação do convite barra antes (409
        # `already_member`). Destino: 3f. O que importa aqui é o que NÃO acontece — o
        # convite não é consumido, porque a função SQL inteira volta atrás.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "email_taken", "message": "Este e-mail já tem conta."},
        ) from exc

    if account is None:
        # Nada foi consumido. Só agora vale a consulta a mais: ela existe para dizer
        # POR QUE, e só o caminho que já falhou a paga.
        stamps = await invitations.stamps_by_token_hash(token_hash)
        verdict = verdict_for(stamps, now=datetime.now(UTC))
        # Carimbos que parecem pendentes depois de um aceite que não consumiu nada só
        # acontecem numa corrida: outra pessoa ganhou o `update` e o commit dela ainda
        # não estava visível nesta leitura. O convite foi usado — por ela.
        raise _invite_error(verdict or (status.HTTP_410_GONE, "invite_used"))

    emit_session_cookie(response, account, request)
    return AcceptedResponse(email=account.email, role=account.role)

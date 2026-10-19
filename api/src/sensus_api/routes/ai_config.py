"""Config de IA por tenant: ver, salvar uma versão nova, voltar para uma anterior.

Três rotas, todas exigindo `admin` — e aqui o papel é `Depends(require_session)` mais
`require_admin` dentro do handler, como em `documents.py` e `invites.py`.

O que **não** está aqui, e é a decisão central da fatia: a política anti-alucinação. As
quatro regras e o sentinela ficam no código (`sensus_rag/generation/prompt.py`), e o
que o tenant edita é um briefing que entra depois delas, subordinado. Prompt inteiro
editável mataria a promessa central do produto por configuração, sem erro nenhum
aparecer — bastaria apagar a regra 2.
"""

import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from sensus_api.ai_configs import AiConfig, AiConfigVersion, ConcurrentConfigWrite
from sensus_api.principal import Principal, require_admin, require_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/ai-config")

# Teto do briefing. Ele divide o orçamento de contexto com os trechos: um briefing de
# 50 KB empurraria a base inteira para fora do prompt, e a falha apareceria como "o bot
# não acha nada" — longe da causa.
BRIEFING_MAX_CHARS = 4_000

# Teto por sugestão. Chip de tela vazia que não cabe na tela não é sugestão.
SUGGESTION_MAX_CHARS = 200

# Página do histórico. O default é o que protege quem não pede nada — o painel, hoje —,
# e o teto é o que impede `?limit=100000` de desfazer a paginação por query string.
VERSIONS_PAGE_DEFAULT = 20
VERSIONS_PAGE_MAX = 100


class ConfigBody(BaseModel):
    briefing: str = Field(default="", max_length=BRIEFING_MAX_CHARS)
    generation_model: str = Field(min_length=1, max_length=120)
    # 1..20: abaixo de 1 não há o que responder; acima de 20 o custo cresce sem que a
    # medição de 2026-09-01 mostre ganho.
    generation_top_k: int = Field(ge=1, le=20)
    max_context_chars: int = Field(ge=1_000, le=50_000)
    # Teto por item, e não só de quantidade: a sugestão vai para TODO membro do tenant
    # a cada carga de página (`/auth/me`), e dez de 50 KB pesam o mesmo que uma de 500.
    suggestions: list[Annotated[str, Field(max_length=SUGGESTION_MAX_CHARS)]] = Field(
        default_factory=list, max_length=10
    )

    def to_config(self) -> AiConfig:
        return AiConfig(
            briefing=self.briefing,
            generation_model=self.generation_model,
            generation_top_k=self.generation_top_k,
            max_context_chars=self.max_context_chars,
            suggestions=tuple(limpa for s in self.suggestions if (limpa := s.strip())),
        )


class ConfigResponse(BaseModel):
    briefing: str
    generation_model: str
    generation_top_k: int
    max_context_chars: int
    suggestions: list[str]


class VersionResponse(BaseModel):
    id: str
    version: int
    is_active: bool
    created_at: datetime
    created_by_email: str | None
    config: ConfigResponse


class AiConfigResponse(BaseModel):
    """O que a tela precisa para não mentir.

    `active` é a config **resolvida** — com os defaults do ambiente quando não há
    versão nenhuma —, porque a tela mostra de onde o comportamento atual vem. Um tenant
    recém-criado vê `versions: []` e um `active` que é o default, e isso é a verdade,
    não um caso especial.
    """

    active: ConfigResponse
    versions: list[VersionResponse]
    # A tela precisa saber se o botão "carregar mais" tem o que carregar. Sem este
    # campo, ela só poderia adivinhar por "veio página cheia" — e erraria justamente
    # quando o total é múltiplo do limite, mostrando um botão que não traz nada.
    has_more_versions: bool
    allowed_models: list[str]


def _config_response(config: AiConfig) -> ConfigResponse:
    return ConfigResponse(
        briefing=config.briefing,
        generation_model=config.generation_model,
        generation_top_k=config.generation_top_k,
        max_context_chars=config.max_context_chars,
        suggestions=list(config.suggestions),
    )


def _version_response(version: AiConfigVersion) -> VersionResponse:
    return VersionResponse(
        id=version.id,
        version=version.version,
        is_active=version.is_active,
        created_at=version.created_at,
        created_by_email=version.created_by_email,
        config=_config_response(version.config),
    )


def _recusa_modelo(permitidos: list[str]) -> HTTPException:
    """A mensagem NOMEIA os permitidos, e é por isso que este código existe separado
    dos limites numéricos: sem a lista, a tela só pode dizer "inválido" e a pessoa fica
    adivinhando."""
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={
            "code": "model_not_allowed",
            "message": f"Modelo não permitido. Use um destes: {', '.join(permitidos)}.",
        },
    )


def _recusa_por_corrida(acao: str) -> HTTPException:
    """409, e não 500: o banco protegeu o dado como devia.

    Os dois caminhos que escrevem — salvar e fazer rollback — disputam índices desta
    mesma tabela, e a mensagem manda RECARREGAR antes de repetir: quem perdeu a corrida
    tem na tela um estado que já não vale, e repetir por cima dele desfaria em silêncio
    o que a outra pessoa acabou de fazer.
    """
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": "config_conflict",
            "message": (
                f"Alguém da sua organização {acao} ao mesmo tempo. Recarregue para ver "
                "o que está valendo e tente de novo."
            ),
        },
    )


@router.get("")
async def read_config(
    request: Request,
    principal: Annotated[Principal, Depends(require_session)],
    limit: Annotated[int, Query(ge=1, le=VERSIONS_PAGE_MAX)] = VERSIONS_PAGE_DEFAULT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AiConfigResponse:
    """O histórico vem PAGINADO; `active` e os modelos, sempre inteiros.

    A página é só do histórico porque é só ele que cresce sem teto — uma linha por
    edição, cada uma com o config completo. Quem abre o painel quer editar a config que
    está valendo, e essa é uma linha só.
    """
    require_admin(principal)
    resolver = request.app.state.ai_config_resolver
    directory = request.app.state.ai_configs

    active = await resolver.resolve(principal.tenant_id)
    pagina = await directory.list_versions(principal.tenant_id, limit=limit, offset=offset)
    return AiConfigResponse(
        active=_config_response(active),
        versions=[_version_response(v) for v in pagina.versions],
        has_more_versions=pagina.has_more,
        allowed_models=list(request.app.state.settings.allowed_generation_models),
    )


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_config(
    payload: ConfigBody,
    request: Request,
    principal: Annotated[Principal, Depends(require_session)],
) -> VersionResponse:
    """Salvar **publica**: cria a versão seguinte e a ativa, num átomo só.

    Não existe rascunho, e a tela diz isso antes de salvar — "salvar" que publica e
    "salvar" que guarda são coisas diferentes, e confundir as duas vira suporte.
    """
    require_admin(principal)
    permitidos = list(request.app.state.settings.allowed_generation_models)
    if payload.generation_model not in permitidos:
        # Modelo inexistente viraria 4xx do provedor em produção, descoberto pelo
        # usuário final no meio de uma resposta. Recusar aqui é o único lugar onde o
        # erro ainda tem a quem ser mostrado.
        raise _recusa_modelo(permitidos)

    try:
        criada = await request.app.state.ai_configs.create_version(
            tenant_id=principal.tenant_id,
            created_by=principal.user_id,
            config=payload.to_config(),
        )
    except ConcurrentConfigWrite:
        # Dois admins da mesma organização salvando no mesmo instante. O banco recusou
        # a segunda, e é isso que a pessoa precisa saber: a versão que está valendo não
        # é a que ela tinha na tela, então tentar de novo sem reler gravaria por cima
        # do que a outra acabou de publicar.
        raise _recusa_por_corrida("salvou a configuração") from None
    logger.info(
        "config de IA salva: tenant=%s versao=%s por=%s",
        principal.tenant_id,
        criada.version,
        principal.user_id,
    )
    return _version_response(criada)


@router.post("/{config_id}/activate", status_code=status.HTTP_204_NO_CONTENT)
async def activate_config(
    config_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(require_session)],
) -> None:
    """Rollback: volta o comportamento para uma versão anterior, sem apagar nenhuma.

    Versão inexistente e versão de outra organização respondem o MESMO 404 — a policy
    não distingue as duas, e a borda também não deveria: distinguir transformaria a
    rota num oráculo de quais ids existem por aí.
    """
    require_admin(principal)
    try:
        trocou = await request.app.state.ai_configs.activate(
            tenant_id=principal.tenant_id, config_id=config_id
        )
    except ConcurrentConfigWrite:
        # Dois rollbacks cruzados: o índice de "uma ativa por tenant" recusou o segundo.
        raise _recusa_por_corrida("trocou a versão ativa") from None
    if not trocou:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "Esta versão não existe."},
        )
    logger.info(
        "config de IA ativada: tenant=%s versao_id=%s por=%s",
        principal.tenant_id,
        config_id,
        principal.user_id,
    )

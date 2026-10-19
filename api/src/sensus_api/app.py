"""Fábrica da aplicação FastAPI do adaptador."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from sensus_api.ai_config_resolver import AiConfigResolver
from sensus_api.ai_configs import AiConfig, AiConfigDirectory
from sensus_api.ai_configs_memory import InMemoryAiConfigDirectory
from sensus_api.ai_configs_postgres import PostgresAiConfigDirectory
from sensus_api.config import Settings, get_settings
from sensus_api.email import EmailSender, LoggingEmailSender
from sensus_api.email_smtp import SmtpEmailSender
from sensus_api.invitations import InvitationDirectory
from sensus_api.invitations_memory import InMemoryInvitationDirectory
from sensus_api.invitations_postgres import PostgresInvitationDirectory
from sensus_api.password_resets import PasswordResetDirectory
from sensus_api.password_resets_memory import InMemoryPasswordResetDirectory
from sensus_api.password_resets_postgres import PostgresPasswordResetDirectory
from sensus_api.pending_signups import PendingSignupDirectory
from sensus_api.pending_signups_memory import InMemoryPendingSignupDirectory
from sensus_api.pending_signups_postgres import PostgresPendingSignupDirectory
from sensus_api.rag_wiring import RagResources, build_rag
from sensus_api.routes import ai_config as ai_config_routes
from sensus_api.routes import auth as auth_routes
from sensus_api.routes import chat as chat_routes
from sensus_api.routes import conversation as conversation_routes
from sensus_api.routes import documents as document_routes
from sensus_api.routes import feedback as feedback_routes
from sensus_api.routes import invites as invite_routes
from sensus_api.routes import password_reset as password_reset_routes
from sensus_api.routes import search as search_routes
from sensus_api.routes import signup_confirmation as signup_confirmation_routes
from sensus_api.store import ConversationStore, InMemoryConversationStore
from sensus_api.upstream.contract import UpstreamClient
from sensus_api.upstream.demo import DemoUpstream
from sensus_api.upstream.rag_upstream import RagUpstream
from sensus_api.users import UserDirectory
from sensus_api.users_memory import InMemoryUserDirectory
from sensus_api.users_postgres import PostgresUserDirectory
from sensus_rag.config import get_rag_settings
from sensus_rag.contract import ChunkRepository, Retriever
from sensus_rag.ingestion import DocumentIngestor
from sensus_rag.tenant_scope import TenantScope

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _rag_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Abre o pool/cliente HTTP do pipeline real na subida, fecha na descida.

    Inerte no pipeline em memória: `app.state.rag_resources` vem vazio (todos os
    campos `None`) sempre que `create_app` recebeu as três peças já montadas — é
    o caso de todo teste que injeta `ingestor`/`retriever`/`repository`, e nenhum
    deles abre conexão.
    """
    resources: RagResources = app.state.rag_resources
    await resources.open()
    try:
        yield
    finally:
        await resources.close()


def create_app(
    settings: Settings | None = None,
    *,
    upstream: UpstreamClient | None = None,
    store: ConversationStore | None = None,
    ingestor: DocumentIngestor | None = None,
    retriever: Retriever | None = None,
    repository: ChunkRepository | None = None,
    email: EmailSender | None = None,
) -> FastAPI:
    """Cria a aplicação. `upstream` e `store` explícitos existem para teste.

    Sem `upstream` injetado, a aplicação monta o pipeline real (`build_rag`) e escolhe
    entre `RagUpstream` — quando a geração está configurada — e `DemoUpstream`, modo
    demonstração com texto fixo, caso contrário.
    """
    resolved = settings or get_settings()
    app = FastAPI(title="Sensus IA — API", lifespan=_rag_lifespan)
    app.state.settings = resolved
    app.state.store = store if store is not None else InMemoryConversationStore()

    # Sem peças injetadas, a montagem decide entre o pipeline real e o de memória
    # conforme a configuração (ver `rag_wiring.build_rag`). `rag_resources` carrega o
    # pool e os clientes HTTP que o lifespan acima abre/fecha — vazio quando as três
    # peças chegam injetadas, porque `build_rag` nem roda.
    rag_settings = get_rag_settings()
    pipeline = None
    rag_resources = RagResources()
    if ingestor is None or retriever is None or repository is None:
        pipeline = build_rag(rag_settings)
        ingestor = ingestor or pipeline.ingestor
        retriever = retriever or pipeline.retriever
        repository = repository or pipeline.repository
        rag_resources = pipeline.resources
    app.state.ingestor = ingestor
    app.state.retriever = retriever
    app.state.repository = repository
    app.state.rag_resources = rag_resources

    # O diretório de usuários segue o MESMO pool do pipeline: com banco real, o
    # diretório é o do Postgres; sem ele, o de memória. Duas decisões separadas
    # divergiriam, e a aplicação subiria com chat real e login de mentira. O de
    # convites acompanha, pelo mesmo motivo.
    users: UserDirectory
    invitations: InvitationDirectory
    resets: PasswordResetDirectory
    pendings: PendingSignupDirectory
    ai_configs: AiConfigDirectory
    if rag_resources.pool is not None:
        users = PostgresUserDirectory(pool=rag_resources.pool)
        # As duas portas do adaptador de convites, do MESMO pool: `TenantScope` para o
        # lado do admin (a policy isola) e o pool cru para as frestas do convidado,
        # que não tem tenant a informar.
        invitations = PostgresInvitationDirectory(
            scope=TenantScope(pool=rag_resources.pool), pool=rag_resources.pool
        )
        # Só o pool cru: as três rotas do reset são anônimas, e não há lado com tenant
        # para um `TenantScope` proteger.
        resets = PostgresPasswordResetDirectory(pool=rag_resources.pool)
        # Só o pool cru, pela mesma razão do reset — e aqui ela é mais forte: no
        # pedido de cadastro o tenant ainda NEM EXISTE.
        pendings = PostgresPendingSignupDirectory(pool=rag_resources.pool)
        # Só `TenantScope`, sem pool cru: ao contrário do convite e do reset, TODO
        # caminho que toca config tem tenant — o admin editando e o chat
        # resolvendo. Não há fresta `security definer` nenhuma na migração 010.
        ai_configs = PostgresAiConfigDirectory(scope=TenantScope(pool=rag_resources.pool))
    else:
        memory_users = InMemoryUserDirectory()
        users = memory_users
        # A MESMA instância: aceitar convite cria conta, e dois diretórios de usuários
        # fariam a pessoa nascer onde o login não a encontra.
        invitations = InMemoryInvitationDirectory(users=memory_users)
        # A MESMA instância, pelo mesmo motivo: o reset procura a conta que o cadastro
        # criou, e dois diretórios de usuários a fariam procurar onde ela não está.
        resets = InMemoryPasswordResetDirectory(users=memory_users)
        # A MESMA instância, de novo: confirmar cadastro CRIA conta, e dois
        # diretórios de usuários a fariam nascer onde o login não a encontra.
        pendings = InMemoryPendingSignupDirectory(users=memory_users)
        # A MESMA instância de usuários: a versão guarda quem a criou, e o duplo
        # resolve esse e-mail por um `find_by_id` que precisa achar a pessoa.
        ai_configs = InMemoryAiConfigDirectory(users=memory_users)
    app.state.users = users
    app.state.invitations = invitations
    app.state.password_resets = resets
    app.state.pending_signups = pendings
    app.state.ai_configs = ai_configs
    # Os defaults do AMBIENTE, que valem para todo tenant que nunca salvou config.
    # É aqui que as duas metades se encontram: o diretório (banco) e a política de
    # default (produto).
    app.state.ai_config_defaults = AiConfig(
        briefing="",
        generation_model=rag_settings.generation_model,
        generation_top_k=rag_settings.generation_top_k,
        max_context_chars=rag_settings.generation_max_context_chars,
        suggestions=tuple(resolved.suggestions),
    )
    if rag_settings.generation_model not in resolved.allowed_generation_models:
        # O default do ambiente é o que vale para quem nunca salvou config. Fora da
        # lista permitida, o painel abre com um `select` sem opção correspondente e
        # salvar sem tocar no modelo dá 422 — um erro do operador aparecendo na tela
        # do cliente. O aviso ao subir é o barulho no lugar certo, como o do SMTP.
        logger.warning(
            "SENSUS_GENERATION_MODEL (%s) está fora de SENSUS_ALLOWED_GENERATION_MODELS "
            "(%s): o painel de config de IA não conseguirá salvar sem trocar o modelo",
            rag_settings.generation_model,
            ", ".join(resolved.allowed_generation_models),
        )
    ai_config_resolver = AiConfigResolver(
        directory=ai_configs, defaults=app.state.ai_config_defaults
    )
    app.state.ai_config_resolver = ai_config_resolver

    # O provedor de e-mail não acompanha o pool: ele depende de haver SMTP configurado,
    # que é decisão independente de haver banco.
    sender: EmailSender
    if email is not None:
        sender = email
    elif resolved.smtp_host:
        sender = SmtpEmailSender(
            host=resolved.smtp_host,
            port=resolved.smtp_port,
            username=resolved.smtp_username,
            password=resolved.smtp_password,
            starttls=resolved.smtp_starttls,
            sender=resolved.email_from,
        )
    else:
        # Sem SMTP o e-mail vai para o log, com link e tudo. É o que faz o fluxo rodar
        # numa máquina de desenvolvimento — e, em produção, é um sintoma: ninguém
        # recebe nada. Por isso o aviso ao subir, e não um silêncio.
        logger.warning("SENSUS_SMTP_HOST não configurado: e-mails vão para o log, não para ninguém")
        sender = LoggingEmailSender()
    app.state.email = sender

    # O upstream escolhido depende do RAG (retriever + gerador) acabado de montar
    # acima — por isso este bloco vem depois, e não antes.
    if upstream is None:
        generator = pipeline.generator if pipeline is not None else None
        if generator is not None:
            upstream = RagUpstream(
                retriever=retriever,
                generator=generator,
                configs=ai_config_resolver,
            )
        else:
            logger.warning("modo demonstração: geração não configurada, as respostas são fixas")
            upstream = DemoUpstream()
    app.state.upstream = upstream

    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.cors_origins,
        allow_credentials=True,  # a sessão é cookie httpOnly
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type"],
    )

    @app.get("/api/v1/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(ai_config_routes.router)
    app.include_router(auth_routes.router)
    app.include_router(chat_routes.router)
    app.include_router(conversation_routes.router)
    app.include_router(feedback_routes.router)
    app.include_router(document_routes.router)
    app.include_router(invite_routes.router)
    app.include_router(password_reset_routes.router)
    app.include_router(search_routes.router)
    app.include_router(signup_confirmation_routes.router)
    return app

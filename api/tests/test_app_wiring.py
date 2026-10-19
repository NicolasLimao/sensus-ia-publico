"""A montagem escolhe as peças de verdade quando há configuração, e as de memória
quando não há. Nenhum teste aqui abre conexão: os recursos do pipeline real
(`AsyncConnectionPool`, `httpx.AsyncClient`) são só instanciados, nunca abertos —
`AsyncConnectionPool(..., open=False)` só conecta em `.open()`, chamado pelo
`lifespan` da aplicação, não por `build_rag` nem por este módulo."""

import logging

import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from conftest import settings_sem_env, tls_client
from sensus_api.app import create_app
from sensus_api.config import Settings
from sensus_api.rag_wiring import build_rag, uses_real_pipeline, uses_rerank
from sensus_rag.config import RagSettings
from sensus_rag.embedding.voyage import VoyageEmbedder
from sensus_rag.repository.memory import InMemoryChunkRepository
from sensus_rag.repository.postgres import PostgresChunkRepository
from sensus_rag.reranking.voyage import VoyageReranker
from sensus_rag.retrieval import HybridRetriever, RerankingRetriever


def test_sem_configuracao_fica_em_memoria() -> None:
    assert uses_real_pipeline(RagSettings(database_url="", voyage_api_key="")) is False


def test_so_com_banco_ainda_fica_em_memoria() -> None:
    assert uses_real_pipeline(RagSettings(database_url="postgres://x", voyage_api_key="")) is False


def test_com_banco_e_chave_usa_o_pipeline_real() -> None:
    assert uses_real_pipeline(RagSettings(database_url="postgres://x", voyage_api_key="k")) is True


def test_pipeline_em_memoria_nao_tem_recursos_para_fechar() -> None:
    pipeline = build_rag(RagSettings(database_url="", voyage_api_key=""))
    assert isinstance(pipeline.repository, InMemoryChunkRepository)
    assert pipeline.resources.pool is None
    assert pipeline.resources.http_client is None


async def test_pipeline_real_monta_recursos_fechaveis_sem_conectar() -> None:
    pipeline = build_rag(RagSettings(database_url="postgres://x", voyage_api_key="k"))
    assert isinstance(pipeline.repository, PostgresChunkRepository)
    assert pipeline.resources.pool is not None
    assert pipeline.resources.http_client is not None
    # Fecha sem nunca ter aberto — mesmo caminho que o lifespan percorre quando a
    # aplicação sobe e desce sem servir nenhuma rota. Não deve tocar rede: o pool
    # nasce com `open=False`, então já nasce fechado, e `close()` é no-op nesse caso.
    await pipeline.resources.close()


async def test_open_e_close_em_recursos_vazios_nao_fazem_nada() -> None:
    pipeline = build_rag(RagSettings(database_url="", voyage_api_key=""))
    await pipeline.resources.open()
    await pipeline.resources.close()


def test_create_app_sem_pecas_injetadas_fica_em_memoria_na_suite_padrao() -> None:
    """Trava a garantia da fixture `_sanitize_rag_env` (`conftest.py`): mesmo que o
    `.env` do desenvolvedor tenha `SENSUS_DATABASE_URL` e `SENSUS_VOYAGE_API_KEY`,
    `create_app()` sem peças de RAG injetadas resolve para o pipeline em memória
    sob a suíte padrão. Sem este teste, a proteção se perde na primeira
    refatoração do `conftest` sem ninguém notar."""
    app = create_app(Settings(session_secret="s", suggestions=["s?"]))
    assert isinstance(app.state.repository, InMemoryChunkRepository)


async def test_com_geracao_configurada_sobe_o_upstream_real(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Quem configurou chave de verdade não pode receber resposta de demonstração."""
    from sensus_api.app import create_app
    from sensus_api.config import Settings
    from sensus_api.upstream.rag_upstream import RagUpstream
    from sensus_rag.config import get_rag_settings

    get_rag_settings.cache_clear()
    monkeypatch.setenv("SENSUS_OPENAI_API_KEY", "chave-de-teste")
    monkeypatch.setenv("SENSUS_GENERATION_MODEL", "gpt-4o-mini")
    try:
        app = create_app(Settings(session_secret="s"))
        assert isinstance(app.state.upstream, RagUpstream)
    finally:
        get_rag_settings.cache_clear()


async def test_sem_geracao_configurada_segue_em_demonstracao(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sensus_api.app import create_app
    from sensus_api.config import Settings
    from sensus_api.upstream.demo import DemoUpstream
    from sensus_rag.config import get_rag_settings

    get_rag_settings.cache_clear()
    monkeypatch.setenv("SENSUS_OPENAI_API_KEY", "")
    try:
        app = create_app(Settings(session_secret="s"))
        assert isinstance(app.state.upstream, DemoUpstream)
    finally:
        get_rag_settings.cache_clear()


async def test_o_cliente_de_geracao_e_fechado_na_descida(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sensus_api.app import create_app
    from sensus_api.config import Settings
    from sensus_rag.config import get_rag_settings

    get_rag_settings.cache_clear()
    monkeypatch.setenv("SENSUS_OPENAI_API_KEY", "chave-de-teste")
    monkeypatch.setenv("SENSUS_GENERATION_MODEL", "gpt-4o-mini")
    try:
        app = create_app(Settings(session_secret="s"))
        client = app.state.rag_resources.generation_client
        assert client is not None
        with tls_client(app):
            pass
        assert client.is_closed
    finally:
        get_rag_settings.cache_clear()


def test_o_diretorio_de_convites_acompanha_o_de_usuarios() -> None:
    """Duas decisões separadas divergiriam, e a aplicação subiria com login de verdade
    e convite de mentira — o mesmo motivo pelo qual o diretório de usuários segue o
    pool do pipeline."""
    from sensus_api.invitations_memory import InMemoryInvitationDirectory
    from sensus_api.users_memory import InMemoryUserDirectory

    app = create_app(Settings(session_secret="s"))

    assert isinstance(app.state.users, InMemoryUserDirectory)
    assert isinstance(app.state.invitations, InMemoryInvitationDirectory)
    # A MESMA instância, não uma igual: aceitar convite cria conta, e dois diretórios
    # de usuários fariam a pessoa nascer onde o login não a encontra.
    assert app.state.invitations.users is app.state.users


async def test_com_banco_o_convite_usa_o_postgres_e_o_mesmo_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """O adaptador real precisa das duas portas — `TenantScope` para o admin e o pool
    cru para as frestas — e as duas têm de sair do MESMO pool que o lifespan abre e
    fecha. Um pool próprio nunca seria aberto, e todo convite viraria erro de conexão.
    """
    from sensus_api.invitations_postgres import PostgresInvitationDirectory
    from sensus_rag.config import get_rag_settings

    get_rag_settings.cache_clear()
    monkeypatch.setenv("SENSUS_DATABASE_URL", "postgres://x")
    monkeypatch.setenv("SENSUS_VOYAGE_API_KEY", "k")
    try:
        app = create_app(Settings(session_secret="s"))
        invitations = app.state.invitations
        assert isinstance(invitations, PostgresInvitationDirectory)
        assert invitations.pool is app.state.rag_resources.pool
        assert invitations.scope.pool is app.state.rag_resources.pool
    finally:
        get_rag_settings.cache_clear()


async def test_com_banco_o_reset_usa_o_postgres_e_o_mesmo_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Irmão do teste dos convites, e existe porque a ausência desta fiação foi pega
    por acidente — pelo teste do convite, que só quebrou porque `resets` ficava sem
    atribuição no ramo com banco. Um acidente não é uma âncora.

    Diferente do convite, aqui é SÓ o pool cru: as três rotas do reset são anônimas, e
    não há lado com tenant para um `TenantScope` proteger.
    """
    from sensus_api.password_resets_postgres import PostgresPasswordResetDirectory
    from sensus_rag.config import get_rag_settings

    get_rag_settings.cache_clear()
    monkeypatch.setenv("SENSUS_DATABASE_URL", "postgres://x")
    monkeypatch.setenv("SENSUS_VOYAGE_API_KEY", "k")
    try:
        app = create_app(Settings(session_secret="s"))
        resets = app.state.password_resets
        assert isinstance(resets, PostgresPasswordResetDirectory)
        assert resets.pool is app.state.rag_resources.pool
    finally:
        get_rag_settings.cache_clear()


async def test_com_banco_as_pendencias_de_cadastro_usam_o_postgres_e_o_mesmo_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Irmão do teste do reset, e pela mesma razão: só o pool cru. No pedido de
    cadastro o tenant ainda NEM EXISTE — é a confirmação que o cria."""
    from sensus_api.pending_signups_postgres import PostgresPendingSignupDirectory
    from sensus_rag.config import get_rag_settings

    get_rag_settings.cache_clear()
    monkeypatch.setenv("SENSUS_DATABASE_URL", "postgres://x")
    monkeypatch.setenv("SENSUS_VOYAGE_API_KEY", "k")
    try:
        app = create_app(Settings(session_secret="s"))
        pendings = app.state.pending_signups
        assert isinstance(pendings, PostgresPendingSignupDirectory)
        assert pendings.pool is app.state.rag_resources.pool
    finally:
        get_rag_settings.cache_clear()


def test_sem_banco_as_pendencias_compartilham_o_diretorio_de_usuarios(
    app: FastAPI,
) -> None:
    """Confirmar cadastro CRIA conta: dois diretórios de usuários fariam a pessoa
    nascer onde o login não a encontra."""
    from sensus_api.pending_signups_memory import InMemoryPendingSignupDirectory

    pendings = app.state.pending_signups
    assert isinstance(pendings, InMemoryPendingSignupDirectory)
    assert pendings.users is app.state.users


def test_sem_smtp_o_provedor_de_email_e_o_de_log() -> None:
    """Escolha independente do banco: e-mail depende de haver SMTP, não de haver
    Postgres."""
    from sensus_api.email import LoggingEmailSender

    app = create_app(settings_sem_env(session_secret="s"))

    assert isinstance(app.state.email, LoggingEmailSender)


def test_com_smtp_o_provedor_e_o_real() -> None:
    from sensus_api.email_smtp import SmtpEmailSender

    app = create_app(
        settings_sem_env(
            session_secret="s",
            smtp_host="smtp.exemplo.invalido",
            email_from="nao-responda@exemplo.invalido",
            public_base_url="https://sensus.invalido",
        )
    )

    sender = app.state.email
    assert isinstance(sender, SmtpEmailSender)
    assert sender.host == "smtp.exemplo.invalido"
    assert sender.sender == "nao-responda@exemplo.invalido"


def test_o_rerank_vem_ligado_por_default() -> None:
    """A medição de 2026-09-05 (recall@1 +0,192, quase 5x a margem de ruído) é o que
    paga esse default: sair da caixa sem rerank seria entregar de propósito a versão
    pior da recuperação. Desligar continua possível, mas passa a ser explícito."""
    assert uses_rerank(RagSettings()) is True


def test_sem_modelo_de_rerank_o_rerank_fica_desligado() -> None:
    assert uses_rerank(RagSettings(rerank_model="")) is False


def test_com_modelo_de_rerank_o_rerank_liga() -> None:
    assert uses_rerank(RagSettings(rerank_model="rerank-2.5")) is True


def test_sem_rerank_o_retriever_e_o_hibrido_nu() -> None:
    pipeline = build_rag(
        RagSettings(database_url="postgres://x", voyage_api_key="k", rerank_model="")
    )

    assert isinstance(pipeline.retriever, HybridRetriever)


def test_com_rerank_o_hibrido_e_embrulhado() -> None:
    pipeline = build_rag(
        RagSettings(database_url="postgres://x", voyage_api_key="k", rerank_model="rerank-2.5")
    )

    assert isinstance(pipeline.retriever, RerankingRetriever)
    assert isinstance(pipeline.retriever.inner, HybridRetriever)


def test_embedder_e_reranker_dividem_a_MESMA_instancia_de_limitador() -> None:
    """São a mesma conta Voyage. Dois limitadores independentes acham cada um que têm
    3 RPM inteiros — a suíte passa verde e a rodada real toma 429. Identidade, não
    igualdade: dois limitadores com a mesma configuração seriam justamente o bug."""
    pipeline = build_rag(
        RagSettings(
            database_url="postgres://x",
            voyage_api_key="k",
            rerank_model="rerank-2.5",
            embedding_requests_per_minute=3,
            embedding_tokens_per_minute=10_000,
        )
    )

    assert isinstance(pipeline.retriever, RerankingRetriever)
    reranker = pipeline.retriever.reranker
    inner = pipeline.retriever.inner
    assert isinstance(reranker, VoyageReranker)
    assert isinstance(inner, HybridRetriever)
    assert isinstance(inner.embedder, VoyageEmbedder)
    assert reranker.limiter is inner.embedder.limiter
    assert reranker.limiter is not None


def test_pipeline_em_memoria_ignora_rerank_model_e_nao_fala_com_a_voyage() -> None:
    """Sem banco e chave, o pipeline é o em memória — mesmo com `rerank_model`
    preenchido no ambiente, não pode montar um `VoyageReranker` de rede."""
    pipeline = build_rag(RagSettings(database_url="", voyage_api_key="", rerank_model="rerank-2.5"))

    assert isinstance(pipeline.retriever, HybridRetriever)


class TestSettingsDeEmail:
    """Com SMTP configurado, remetente e base pública viram obrigatórios: sem eles a
    aplicação subiria mandando e-mail sem remetente ou com link para lugar nenhum, e o
    único lugar onde isso apareceria é a caixa de entrada de quem precisava do link."""

    def test_smtp_sem_public_base_url_recusa_subir(self) -> None:
        with pytest.raises(ValidationError, match="SENSUS_PUBLIC_BASE_URL"):
            settings_sem_env(
                session_secret="s",
                smtp_host="smtp.exemplo.invalido",
                email_from="nao-responda@exemplo.invalido",
            )

    def test_smtp_sem_remetente_recusa_subir(self) -> None:
        with pytest.raises(ValidationError, match="SENSUS_EMAIL_FROM"):
            settings_sem_env(session_secret="s", smtp_host="smtp.exemplo.invalido")

    def test_sem_smtp_os_dois_sao_opcionais(self) -> None:
        """Desenvolvimento sem SMTP é o caso normal, e não pode exigir configuração de
        e-mail para subir."""
        # `settings_sem_env` e não `Settings`: sem ele o teste leria o `api/.env` da
        # máquina e passaria a falhar no dia em que alguém configurasse SMTP de
        # verdade — um teste que depende da configuração de quem o roda não afirma
        # nada (mesmo furo de `25bb0e5`).
        settings = settings_sem_env(session_secret="s")

        assert settings.smtp_host == ""
        assert settings.public_base_url == ""


class TestDefaultDoAmbienteForaDaLista:
    def test_avisa_ao_subir_quando_o_modelo_default_nao_e_permitido(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """O default do ambiente é o que vale para quem nunca salvou config. Fora da
        lista, o painel abre com um `select` sem opção correspondente e salvar sem
        tocar no modelo dá 422 — erro do operador aparecendo na tela do cliente. Como
        o aviso de SMTP da 3d, o barulho fica no lugar onde dá para consertar.
        """
        from sensus_rag.config import get_rag_settings

        monkeypatch.setenv("SENSUS_GENERATION_MODEL", "gpt-4o-mini")
        get_rag_settings.cache_clear()

        with caplog.at_level(logging.WARNING, logger="sensus_api.app"):
            create_app(settings_sem_env(session_secret="s", allowed_generation_models=["gpt-4o"]))

        get_rag_settings.cache_clear()
        assert "SENSUS_ALLOWED_GENERATION_MODELS" in caplog.text

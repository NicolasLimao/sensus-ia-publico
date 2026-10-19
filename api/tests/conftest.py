"""Fixtures compartilhadas dos testes do adaptador."""

import asyncio
import re
import sys
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response

from sensus_api.app import create_app
from sensus_api.config import Settings
from sensus_api.email_memory import RecordingEmailSender
from sensus_api.store import InMemoryConversationStore
from sensus_api.upstream.contract import Chunk, ResultEvent, StageEvent, TokenEvent
from sensus_api.upstream.fake import ScriptedUpstream
from sensus_rag.chunking import StructuralChunker
from sensus_rag.config import RagSettings, get_rag_settings
from sensus_rag.embedding.fake import FakeEmbedder
from sensus_rag.ingestion import DocumentIngestor
from sensus_rag.repository.memory import InMemoryChunkRepository
from sensus_rag.retrieval import HybridRetriever

RAG_EMBEDDER = FakeEmbedder(dimensions=16)

# No Windows, o loop padrão do asyncio (ProactorEventLoop) não roda o psycopg em modo
# assíncrono: a conexão ao Postgres real (Task 5) falha com InterfaceError antes de
# tentar autenticar. Trocar a policy para SelectorEventLoop resolve, e não afeta
# nenhuma outra plataforma. Sem este ajuste, todo teste sob `-m integration` quebra
# aqui no Windows, mesmo com a task inteira correta.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@pytest.fixture(autouse=True)
def _sanitize_rag_env(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """A suíte padrão não pode montar o pipeline real, não importa o que tenha no
    `.env` do desenvolvedor.

    `create_app()` sem peças de RAG injetadas cai em `build_rag(get_rag_settings())`,
    que lê o ambiente de verdade — inclusive o `.env` local. Hoje isso é inofensivo
    porque falta `SENSUS_VOYAGE_API_KEY` na máquina de quem escreveu isto, mas essa
    chave é obrigatória para o harness de medição (próxima etapa desta fatia) e vai
    ser acrescentada em breve. No instante em que `SENSUS_DATABASE_URL` e
    `SENSUS_VOYAGE_API_KEY` coexistirem no `.env`, um `pytest -q` comum passaria a
    abrir uma conexão de verdade com o Supabase do desenvolvedor durante testes que
    não têm nada a ver com banco — e o sintoma (testes de outra fatia lentos ou
    falhando) esconde a causa. Esta fixture zera as duas variáveis para todo teste
    fora de `-m integration`, que é quem de fato precisa do banco real.

    `get_rag_settings` é `@lru_cache`: sem limpar o cache antes e depois, o valor
    lido uma vez (real ou sanitizado) vazaria para os testes seguintes.

    Com a fatia 2b a chave da OpenAI entra na mesma conta: sem zerá-la, um
    `pytest -q` na máquina de quem tem `SENSUS_OPENAI_API_KEY` no `.env` subiria o
    `RagUpstream` e chamaria o provedor de verdade em testes que não têm nada a ver
    com geração — cobrando pela rodada e falhando sem rede.

    Marcadores `contract` e `integration` rodam só quando alguém pede explicitamente
    (fora da suíte padrão) — quem pede quer o provedor de verdade. Não sabotar.
    """
    if "integration" in request.keywords or "contract" in request.keywords:
        # Estes marcadores rodam só quando alguém pede explicitamente (fora da suíte
        # padrão) e precisam dos provedores de verdade — não sabotar.
        yield
        return

    get_rag_settings.cache_clear()
    monkeypatch.setenv("SENSUS_DATABASE_URL", "")
    monkeypatch.setenv("SENSUS_APP_DATABASE_URL", "")
    monkeypatch.setenv("SENSUS_VOYAGE_API_KEY", "")
    monkeypatch.setenv("SENSUS_OPENAI_API_KEY", "")
    try:
        yield
    finally:
        get_rag_settings.cache_clear()


def settings_sem_env(**campos: Any) -> Settings:
    """`Settings` sem ler o `api/.env` da máquina.

    Um teste cujo resultado depende da configuração de quem o roda não afirma nada —
    é o furo que `25bb0e5` fechou para os testes de contrato.
    """
    # `_env_file` é kwarg de runtime do pydantic-settings; o mypy não o enxerga.
    return Settings(_env_file=None, **campos)  # type: ignore[call-arg]


@pytest.fixture
def settings() -> Settings:
    """Settings determinísticas — nenhum valor real de ambiente.

    `_env_file=None` é o que torna a frase verdadeira: sem ele, `Settings` lê o
    `api/.env` da máquina, e um teste que depende da configuração de quem o roda não
    afirma nada. É o mesmo furo que `25bb0e5` fechou para os testes de contrato.
    """
    return settings_sem_env(
        session_secret="test-secret-not-a-real-one",
        suggestions=["Pergunta um?", "Pergunta dois?", "Pergunta três?"],
        # Desde a fatia 3f o cadastro passa por um link de confirmação, e é daqui
        # que ele sai: sem base pública, o e-mail levaria um link sem host e o
        # helper `cadastrar` não teria token para ler.
        public_base_url="https://sensus.invalido",
    )


@pytest.fixture
def store() -> InMemoryConversationStore:
    return InMemoryConversationStore()


@pytest.fixture
def upstream() -> ScriptedUpstream:
    """Roteiro padrão: uma resposta fundamentada em um trecho."""
    return ScriptedUpstream(
        [
            StageEvent("retrieving"),
            StageEvent("generating"),
            TokenEvent("Uma "),
            TokenEvent("resposta."),
            ResultEvent(chunks=(Chunk(id="8231", excerpt="trecho", score=0.31),), score=0.31),
        ]
    )


@pytest.fixture
def repository() -> InMemoryChunkRepository:
    return InMemoryChunkRepository()


@pytest.fixture
def ingestor(repository: InMemoryChunkRepository) -> DocumentIngestor:
    # Teto baixo de propósito: o teste de 413 precisa de um limite alcançável.
    return DocumentIngestor(
        chunker=StructuralChunker(target_chars=200, overlap_chars=0),
        embedder=RAG_EMBEDDER,
        repository=repository,
        max_bytes=4096,
    )


@pytest.fixture
def retriever(repository: InMemoryChunkRepository) -> HybridRetriever:
    return HybridRetriever(repository=repository, embedder=RAG_EMBEDDER, candidates=20, rrf_k=60)


@pytest.fixture
def app(
    settings: Settings,
    upstream: ScriptedUpstream,
    store: InMemoryConversationStore,
    ingestor: DocumentIngestor,
    retriever: HybridRetriever,
    repository: InMemoryChunkRepository,
) -> FastAPI:
    return create_app(
        settings,
        upstream=upstream,
        store=store,
        ingestor=ingestor,
        retriever=retriever,
        repository=repository,
        # Grava em vez de logar: desde a fatia 3f o cadastro só se completa pelo
        # link, e o e-mail é o ÚNICO lugar onde o token existe.
        email=RecordingEmailSender(),
    )


def tls_client(app: FastAPI) -> TestClient:
    """Cliente sobre `https://`.

    O cookie de sessão é `Secure` por default, e sob `http://` o cliente o descarta —
    nenhum teste de sessão passaria. A produção fala TLS; os testes também. Quem
    exercita o `http://` é `client_sem_tls`, e só ele.
    """
    return TestClient(app, base_url="https://testserver")


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with tls_client(app) as test_client:
        yield test_client


@pytest.fixture
def client_sem_tls(settings: Settings, app: FastAPI) -> Iterator[TestClient]:
    """O dev local: `session_cookie_secure=False` e `http://`, sem TLS."""
    app.state.settings = settings.model_copy(update={"session_cookie_secure": False})
    with TestClient(app, base_url="http://testserver") as test_client:
        yield test_client


# Cadastro padrão dos testes de rota. O cadastro é o único caminho para uma sessão
# desde a fatia 3a — não existe mais sessão anônima —, e desde a 3f são DOIS passos: o
# pedido responde 202 e o link só existe dentro do e-mail.
SENHA_PADRAO = "senha-bem-longa"

_SIGNUP = {"organization_name": "Acme", "email": "ana@acme.com"}

_OUTRO_SIGNUP = {"organization_name": "Globex", "email": "bob@globex.com"}


def cadastrar(
    test_client: TestClient,
    app: FastAPI,
    *,
    organization_name: str,
    email: str,
    password: str = SENHA_PADRAO,
) -> Response:
    """O cadastro inteiro: pedido, token lido do e-mail, confirmação.

    Devolve a resposta da CONFIRMAÇÃO — é ela que emite o cookie de sessão, e há
    testes (os das flags do cookie) cujo assunto é justamente esse cabeçalho.

    Precisa do `app`, e não só do cliente, porque o token sai do `RecordingEmailSender`
    — é o ÚNICO lugar onde ele existe. A resposta do pedido não o carrega, e nenhuma
    rota o devolve; um helper que o pegasse de outro lugar estaria provando outra coisa.
    """
    resposta = test_client.post(
        "/api/v1/auth/signup",
        json={"organization_name": organization_name, "email": email},
    )
    assert resposta.status_code == 202, resposta.text

    corpo = app.state.email.sent[-1].body
    achado = re.search(r"#([A-Za-z0-9_-]+)", corpo)
    assert achado is not None, f"nenhum token no corpo do e-mail: {corpo}"

    resposta = test_client.post(
        "/api/v1/auth/confirm-signup",
        json={"token": achado.group(1), "password": password},
    )
    assert resposta.status_code == 201, resposta.text
    confirmacao: Response = resposta
    return confirmacao


@pytest.fixture
def session_client(app: FastAPI) -> Iterator[TestClient]:
    """Cliente com sessão já emitida — admin de uma organização recém-criada."""
    with tls_client(app) as test_client:
        cadastrar(test_client, app, **_SIGNUP)
        yield test_client


@pytest.fixture
def other_session_client(app: FastAPI) -> Iterator[TestClient]:
    """Admin de OUTRA organização, na MESMA aplicação.

    É assim que o isolamento por tenant se testa desde a fatia 3a: dois clientes
    autenticados de verdade, e não o mesmo cliente trocando um cabeçalho — o cabeçalho
    era a brecha, e não existe mais.
    """
    with tls_client(app) as test_client:
        cadastrar(test_client, app, **_OUTRO_SIGNUP)
        yield test_client


@pytest.fixture
def member_client(app: FastAPI, session_client: TestClient) -> Iterator[TestClient]:
    """Cliente autenticado como `member` da MESMA organização do `session_client`.

    Passa pelo fluxo real de convite — criar, ler o token do e-mail, aceitar — porque é
    o único caminho que existe para nascer `member` desde a fatia 3c. Forjar a conta
    por dentro do diretório criaria um `member` que o resto do produto não sabe fazer.
    """
    resposta = session_client.post("/api/v1/invites", json={"email": "carla@acme.com"})
    assert resposta.status_code == 201, resposta.text

    achado = re.search(r"#([A-Za-z0-9_-]+)", app.state.email.sent[-1].body)
    assert achado is not None, "nenhum token no e-mail do convite"

    with tls_client(app) as convidada:
        aceite = convidada.post(
            "/api/v1/invites/accept",
            json={"token": achado.group(1), "password": SENHA_PADRAO},
        )
        assert aceite.status_code == 201, aceite.text
        yield convidada


# --- Chaves dos testes de contrato -------------------------------------------------
# Saem das MESMAS settings que a aplicação usa — que leem o `api/.env` — e não de
# `os.environ`. Lendo do ambiente, os testes de contrato pulavam numa máquina que ESTÁ
# configurada, e um contrato que nunca roda é exatamente a divergência silenciosa entre
# duplo e provedor real que ele existe para pegar.


def _contract_setting(field: str) -> str:
    valor: str = getattr(RagSettings(), field)
    if not valor:
        pytest.skip(f"SENSUS_{field.upper()} não configurada — nem no ambiente nem no api/.env")
    return valor


@pytest.fixture
def database_url() -> str:
    return _contract_setting("database_url")


@pytest.fixture
def app_database_url() -> str:
    """URL que conecta como `sensus_app` — o papel SEM bypass de RLS."""
    return _contract_setting("app_database_url")


@pytest.fixture
def voyage_api_key() -> str:
    return _contract_setting("voyage_api_key")


@pytest.fixture
def openai_api_key() -> str:
    return _contract_setting("openai_api_key")


@pytest.fixture
def rerank_model() -> str:
    return _contract_setting("rerank_model")


@pytest.fixture
def generation_model() -> str:
    return _contract_setting("generation_model")

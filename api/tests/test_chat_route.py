import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from conftest import cadastrar, tls_client
from sensus_api.ai_config_resolver import AiConfigResolver
from sensus_api.ai_configs import AiConfig
from sensus_api.ai_configs_memory import InMemoryAiConfigDirectory
from sensus_api.app import create_app
from sensus_api.config import Settings
from sensus_api.email_memory import RecordingEmailSender
from sensus_api.store import InMemoryConversationStore
from sensus_api.upstream.fake import ScriptedUpstream
from sensus_api.upstream.rag_upstream import RagUpstream
from sensus_api.users_memory import InMemoryUserDirectory
from sensus_rag.contract import AnswerRefusal, RetrievedChunk
from sensus_rag.generation.fake import ScriptedGenerator
from sensus_rag.ingestion import DocumentIngestor
from sensus_rag.repository.memory import InMemoryChunkRepository
from sensus_rag.retrieval import HybridRetriever


class _StubRetriever:
    """Sempre devolve um trecho — o suficiente para a geração ser alcançada e
    recusar, em vez de a base vazia recusar sem gastar chamada."""

    async def search(self, *, tenant_id: str, question: str, top_k: int) -> list[RetrievedChunk]:
        return [
            RetrievedChunk(
                chunk_id="c1",
                document_id="d1",
                document_name="cdc.md",
                content="trecho",
                score=0.3,
            )
        ]


def test_chat_sem_sessao_responde_401(app: FastAPI) -> None:
    with tls_client(app) as anonymous:
        assert anonymous.post("/api/v1/chat", json={"message": "oi"}).status_code == 401


def test_chat_emite_sse_na_ordem_do_contrato(session_client: TestClient) -> None:
    with session_client.stream("POST", "/api/v1/chat", json={"message": "pergunta?"}) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        body = "".join(response.iter_text())

    names = [line.removeprefix("event: ") for line in body.split("\n") if line.startswith("event:")]
    assert names == ["stage", "stage", "token", "token", "sources", "done"]


def test_chat_repassa_a_pergunta_e_o_tenant_da_identidade(
    session_client: TestClient, app: FastAPI, upstream: ScriptedUpstream
) -> None:
    """O tenant deixou de sair da configuração: sai da conta de quem está logado."""
    conta = app.state.users.accounts["ana@acme.com"]

    with session_client.stream("POST", "/api/v1/chat", json={"message": "pergunta?"}) as response:
        response.read()

    question, user_id, tenant_id = upstream.calls[0]
    assert question == "pergunta?"
    assert tenant_id == conta.tenant_id
    assert user_id == conta.user_id


def test_chat_rejeita_mensagem_vazia(session_client: TestClient) -> None:
    assert session_client.post("/api/v1/chat", json={"message": "   "}).status_code == 422


def test_chat_com_rag_upstream_real_a_recusa_vira_done_sem_fontes(
    settings: Settings,
    store: InMemoryConversationStore,
    ingestor: DocumentIngestor,
    retriever: HybridRetriever,
    repository: InMemoryChunkRepository,
) -> None:
    """M1: a recusa como `done` (não `error`), com `chunks_used: 0` e `sources: []`,
    é provada no adaptador (`test_rag_upstream.py`) mas nunca ponta a ponta pela rota
    — os outros testes deste arquivo usam `ScriptedUpstream`, que pula `RagUpstream`
    inteiro. Este monta o `RagUpstream` de verdade atrás da rota `/chat`."""
    app = create_app(
        settings,
        upstream=RagUpstream(
            retriever=_StubRetriever(),
            generator=ScriptedGenerator([AnswerRefusal()]),
            # Resolvedor real sobre diretório em memória: sem config salva, ele devolve
            # os defaults — que é exatamente o caminho que todo tenant percorre antes
            # de configurar qualquer coisa.
            configs=AiConfigResolver(
                directory=InMemoryAiConfigDirectory(users=InMemoryUserDirectory()),
                defaults=AiConfig(
                    briefing="",
                    generation_model="gpt-4o-mini",
                    generation_top_k=5,
                    max_context_chars=12_000,
                    suggestions=(),
                ),
            ),
        ),
        store=store,
        ingestor=ingestor,
        retriever=retriever,
        repository=repository,
        # O cadastro passa pelo e-mail desde a 3f, e o `cadastrar` lê o token dele.
        email=RecordingEmailSender(),
    )
    with tls_client(app) as client:
        cadastrar(client, app, organization_name="Acme", email="ana@acme.com")
        with client.stream("POST", "/api/v1/chat", json={"message": "pergunta?"}) as response:
            body = "".join(response.iter_text())

    events = []
    for block in body.split("\n\n"):
        if not block.strip():
            continue
        name_line, data_line = block.splitlines()
        name = name_line.removeprefix("event: ")
        data = json.loads(data_line.removeprefix("data: "))
        events.append((name, data))

    assert events[-1][0] == "done"
    assert events[-1][1]["chunks_used"] == 0

    sources = next(data for name, data in events if name == "sources")
    assert sources["sources"] == []

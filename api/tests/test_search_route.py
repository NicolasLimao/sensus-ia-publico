"""Rota de busca — é por ela que o harness de medição e a depuração passam."""

from dataclasses import dataclass

from fastapi import FastAPI
from fastapi.testclient import TestClient

from sensus_rag.contract import RetrievedChunk


def seed(client: TestClient, name: str, content: str) -> None:
    """O tenant sai da sessão do cliente que faz o upload — não há mais cabeçalho."""
    resposta = client.post(
        "/api/v1/documents",
        files={"file": (name, content.encode(), "text/plain")},
    )
    assert resposta.status_code == 201, resposta.text


def test_busca_devolve_o_trecho_com_o_documento_de_origem(session_client: TestClient) -> None:
    seed(session_client, "manual.txt", "O prazo de reembolso e de 30 dias.")

    response = session_client.post("/api/v1/search", json={"question": "prazo de reembolso"})

    assert response.status_code == 200
    results = response.json()["results"]
    assert results
    assert results[0]["document_name"] == "manual.txt"
    assert "30 dias" in results[0]["excerpt"]
    assert results[0]["chunk_id"]


def test_base_vazia_devolve_lista_vazia(session_client: TestClient) -> None:
    response = session_client.post("/api/v1/search", json={"question": "qualquer coisa"})

    assert response.status_code == 200
    assert response.json()["results"] == []


def test_top_k_limita_o_numero_de_resultados(session_client: TestClient) -> None:
    seed(session_client, "manual.txt", "\n\n".join(f"trecho numero {i}" for i in range(10)))

    response = session_client.post("/api/v1/search", json={"question": "trecho", "top_k": 2})

    assert len(response.json()["results"]) <= 2


def test_pergunta_vazia_e_recusada(session_client: TestClient) -> None:
    assert session_client.post("/api/v1/search", json={"question": "   "}).status_code == 422


def test_busca_nao_atravessa_entre_tenants(
    session_client: TestClient, other_session_client: TestClient
) -> None:
    """As DUAS metades: o outro tenant não acha nada, e o dono acha.

    Só a metade negativa é satisfeita por uma busca que devolvesse sempre lista vazia —
    o isolamento pareceria perfeito justamente porque nada funciona.
    """
    seed(session_client, "a.txt", "O prazo de reembolso e de 30 dias.")

    from_other = other_session_client.post(
        "/api/v1/search", json={"question": "prazo de reembolso"}
    )
    from_owner = session_client.post("/api/v1/search", json={"question": "prazo de reembolso"})

    assert from_other.json()["results"] == []
    assert [r["document_name"] for r in from_owner.json()["results"]] == ["a.txt"]


def test_resultado_expoe_a_posicao_em_cada_perna_da_busca(session_client: TestClient) -> None:
    # Diagnóstico da SPEC §3.2 chegando à borda: sem isso, uma perna morta é
    # indistinguível de relevância baixa no score fundido.
    seed(session_client, "manual.txt", "O prazo de reembolso e de 30 dias.")

    result = session_client.post("/api/v1/search", json={"question": "prazo de reembolso"}).json()

    first = result["results"][0]
    assert "semantic_rank" in first and "lexical_rank" in first
    assert first["semantic_rank"] == 1
    assert first["lexical_rank"] == 1


@dataclass
class RetrieverComDiagnostico:
    """Duplo de teste que retorna resultados com diagnóstico configurável."""

    results: list[RetrievedChunk]

    async def search(self, *, tenant_id: str, question: str, top_k: int) -> list[RetrievedChunk]:
        return self.results[:top_k]


def test_search_expoe_o_diagnostico_do_rerank(app: FastAPI, session_client: TestClient) -> None:
    """Mudança aditiva: cliente que ignora campo novo não percebe diferença, e nenhuma
    versão de contrato é necessária — mesma aposta que deixou `document` passar de
    `null` a preenchido na 2b."""
    app.state.retriever = RetrieverComDiagnostico(
        results=[
            RetrievedChunk(
                chunk_id="c1",
                document_id="d1",
                document_name="lei.md",
                content="trecho",
                score=0.03,
                semantic_rank=1,
                lexical_rank=None,
                rerank_score=0.87,
                fused_rank=7,
            )
        ]
    )

    response = session_client.post("/api/v1/search", json={"question": "prazo"})

    resultado = response.json()["results"][0]
    assert resultado["rerank_score"] == 0.87
    assert resultado["fused_rank"] == 7
    # Os campos antigos não mudaram de nome nem de sentido.
    assert resultado["score"] == 0.03
    assert resultado["semantic_rank"] == 1
    assert resultado["lexical_rank"] is None


def test_sem_rerank_os_campos_saem_nulos(app: FastAPI, session_client: TestClient) -> None:
    """`rerank_score` nulo é o sinal de 'não passou por rerank' — e é o que o
    pipeline em memória, sem rerank configurado, tem de produzir."""
    app.state.retriever = RetrieverComDiagnostico(
        results=[
            RetrievedChunk(
                chunk_id="c1",
                document_id="d1",
                document_name="lei.md",
                content="trecho",
                score=0.03,
            )
        ]
    )

    response = session_client.post("/api/v1/search", json={"question": "prazo"})

    resultado = response.json()["results"][0]
    assert resultado["rerank_score"] is None
    assert resultado["fused_rank"] is None

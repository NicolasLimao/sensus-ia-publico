"""Duplo e cliente real, na MESMA asserção.

Os defeitos mais caros deste projeto vieram de um duplo divergir da implementação real
sem que nenhuma revisão visse os dois lados. Aqui as duas implementações de `Reranker`
respondem às mesmas perguntas, e o `MockTransport` mantém tudo fora da rede — então
roda na suíte padrão, não em `-m contract`.
"""

import httpx
import pytest

from sensus_rag.contract import Reranker, RetrievedChunk
from sensus_rag.reranking.fake import FakeReranker
from sensus_rag.reranking.voyage import VoyageReranker


def chunk(chunk_id: str, content: str) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        document_id="d1",
        document_name="lei.md",
        content=content,
        score=0.5,
    )


def _voyage_espelhando_a_ordem_de_entrada() -> VoyageReranker:
    """Responde como o provedor responderia se concordasse com a ordem recebida:
    relevância decrescente na ordem em que os documentos foram enviados."""

    def respond(request: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(request.content)
        enviados = body["documents"]
        # O provedor real HONRA `top_k` — o duplo tem de honrar também, senão as duas
        # implementações discordam justamente na asserção que este arquivo existe para
        # fazer, e a divergência aparece como "falhou só no parâmetro voyage".
        quantos = min(body["top_k"], len(enviados))
        return httpx.Response(
            200,
            json={
                "data": [{"index": i, "relevance_score": 1.0 - i / 100} for i in range(quantos)],
                "usage": {"total_tokens": 10},
            },
        )

    return VoyageReranker(
        api_key="k",
        model="rerank-2.5",
        client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    )


@pytest.fixture(params=["fake", "voyage"])
def reranker(request: pytest.FixtureRequest) -> Reranker:
    if request.param == "fake":
        return FakeReranker()
    return _voyage_espelhando_a_ordem_de_entrada()


async def test_nunca_devolve_mais_que_o_top_k(reranker: Reranker) -> None:
    chunks = [chunk(f"c{i}", f"trecho {i}") for i in range(10)]

    assert len(await reranker.rerank(question="trecho", chunks=chunks, top_k=3)) <= 3


async def test_lista_vazia_devolve_vazio(reranker: Reranker) -> None:
    assert await reranker.rerank(question="p", chunks=[], top_k=5) == []


async def test_preserva_a_identidade_dos_chunks(reranker: Reranker) -> None:
    """Reordena e corta — nunca inventa item nem troca conteúdo."""
    chunks = [chunk(f"c{i}", f"trecho {i}") for i in range(5)]

    devolvidos = await reranker.rerank(question="trecho", chunks=chunks, top_k=5)

    originais = {c.chunk_id: c.content for c in chunks}
    assert {c.chunk_id for c in devolvidos} <= set(originais)
    assert all(c.content == originais[c.chunk_id] for c in devolvidos)


async def test_preenche_as_duas_ordens(reranker: Reranker) -> None:
    chunks = [chunk(f"c{i}", f"trecho {i}") for i in range(5)]

    devolvidos = await reranker.rerank(question="trecho", chunks=chunks, top_k=5)

    assert all(c.rerank_score is not None for c in devolvidos)
    assert all(c.fused_rank is not None for c in devolvidos)


async def test_preserva_o_score_do_rrf(reranker: Reranker) -> None:
    chunks = [chunk("c1", "trecho")]

    devolvidos = await reranker.rerank(question="trecho", chunks=chunks, top_k=1)

    assert devolvidos[0].score == pytest.approx(0.5)

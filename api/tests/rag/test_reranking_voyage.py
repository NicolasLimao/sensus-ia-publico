"""Cliente de rerank da Voyage, testado inteiro contra `MockTransport` — sem rede.

O provedor devolve ÍNDICE, não documento (`return_documents` fica em `false`): o
mapeamento de volta é feito sobre os `RetrievedChunk` que já temos, sem round-trip de
conteúdo e sem chance de o texto voltar alterado.
"""

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from sensus_rag.contract import RetrievedChunk
from sensus_rag.embedding.voyage import CHARS_PER_TOKEN
from sensus_rag.errors import RerankUnavailable
from sensus_rag.rate_limit import SlidingWindowLimiter
from sensus_rag.reranking.voyage import VoyageReranker


def chunk(chunk_id: str, content: str = "trecho") -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        document_id="d1",
        document_name="lei.md",
        content=content,
        score=0.5,
    )


def reranker_com(
    handler: Callable[[httpx.Request], httpx.Response], **kwargs: Any
) -> VoyageReranker:
    return VoyageReranker(
        api_key="chave-de-teste",
        model="rerank-2.5",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        sleep=_no_sleep,
        **kwargs,
    )


async def _no_sleep(seconds: float) -> None:
    return None


async def test_monta_o_corpo_que_a_api_espera() -> None:
    captured: dict[str, Any] = {}

    def respond(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        assert request.headers["authorization"] == "Bearer chave-de-teste"
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [{"index": 0, "relevance_score": 0.9}],
                "model": "rerank-2.5",
                "usage": {"total_tokens": 12},
            },
        )

    await reranker_com(respond).rerank(question="qual o prazo?", chunks=[chunk("c1")], top_k=1)

    assert captured["model"] == "rerank-2.5"
    assert captured["query"] == "qual o prazo?"
    assert captured["documents"] == ["trecho"]
    assert captured["top_k"] == 1
    assert captured["truncation"] is True


async def test_mapeia_pelo_indice_e_preenche_as_duas_ordens() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 2, "relevance_score": 0.9},
                    {"index": 0, "relevance_score": 0.4},
                ],
                "usage": {"total_tokens": 30},
            },
        )

    ordenados = await reranker_com(respond).rerank(
        question="p",
        chunks=[chunk("c1"), chunk("c2"), chunk("c3")],
        top_k=2,
    )

    assert [c.chunk_id for c in ordenados] == ["c3", "c1"]
    assert [c.fused_rank for c in ordenados] == [3, 1]
    assert ordenados[0].rerank_score == pytest.approx(0.9)


async def test_ordena_por_relevancia_mesmo_se_a_resposta_vier_fora_de_ordem() -> None:
    """A doc diz que vem ordenado por relevância decrescente, mas o embedder já
    aprendeu a não confiar em ordem de resposta (`_sort_by_index`). Aqui a ordem É o
    resultado — confiar seria pior."""

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 0, "relevance_score": 0.1},
                    {"index": 1, "relevance_score": 0.8},
                ],
                "usage": {"total_tokens": 20},
            },
        )

    ordenados = await reranker_com(respond).rerank(
        question="p", chunks=[chunk("c1"), chunk("c2")], top_k=2
    )

    assert [c.chunk_id for c in ordenados] == ["c2", "c1"]


async def test_indice_fora_da_faixa_e_erro_explicito() -> None:
    """Descartar em silêncio corromperia a medição sem deixar sinal."""

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": [{"index": 7, "relevance_score": 0.9}], "usage": {}},
        )

    with pytest.raises(RerankUnavailable):
        await reranker_com(respond).rerank(question="p", chunks=[chunk("c1")], top_k=1)


async def test_lista_vazia_nao_chama_o_provedor() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        raise AssertionError("não deveria chamar o provedor com lista vazia")

    assert await reranker_com(respond).rerank(question="p", chunks=[], top_k=5) == []


async def test_guarda_de_orcamento_corta_a_cauda_antes_de_enviar() -> None:
    captured: dict[str, Any] = {}

    def respond(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200, json={"data": [{"index": 0, "relevance_score": 0.9}], "usage": {}}
        )

    gordo = "a" * (1_000 * CHARS_PER_TOKEN)
    await reranker_com(respond, max_request_tokens=8_000).rerank(
        question="p", chunks=[chunk(f"c{i}", gordo) for i in range(20)], top_k=5
    )

    assert len(captured["documents"]) == 7


async def test_truncagem_nao_altera_o_chunk_devolvido() -> None:
    captured: dict[str, Any] = {}

    def respond(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200, json={"data": [{"index": 0, "relevance_score": 0.9}], "usage": {}}
        )

    inteiro = "a" * 5_000
    ordenados = await reranker_com(respond, max_document_chars=300).rerank(
        question="p", chunks=[chunk("c1", inteiro)], top_k=1
    )

    assert len(captured["documents"][0]) == 300
    assert len(ordenados[0].content) == 5_000


async def test_espaca_pela_cota_antes_de_enviar() -> None:
    pedidos: list[int] = []

    class LimitadorEspiao(SlidingWindowLimiter):
        async def acquire(self, tokens: int) -> None:
            pedidos.append(tokens)

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"data": [{"index": 0, "relevance_score": 0.9}], "usage": {}}
        )

    await reranker_com(respond, limiter=LimitadorEspiao(max_requests=3, max_tokens=10_000)).rerank(
        question="p", chunks=[chunk("c1", "a" * 300)], top_k=1
    )

    assert pedidos and pedidos[0] >= 100


async def test_429_e_retentado() -> None:
    tentativas: list[int] = []

    def respond(request: httpx.Request) -> httpx.Response:
        tentativas.append(1)
        if len(tentativas) == 1:
            return httpx.Response(429, json={"error": "slow down"})
        return httpx.Response(
            200, json={"data": [{"index": 0, "relevance_score": 0.9}], "usage": {}}
        )

    ordenados = await reranker_com(respond).rerank(question="p", chunks=[chunk("c1")], top_k=1)

    assert len(tentativas) == 2
    assert ordenados[0].chunk_id == "c1"


async def test_400_nao_e_retentado() -> None:
    tentativas: list[int] = []

    def respond(request: httpx.Request) -> httpx.Response:
        tentativas.append(1)
        return httpx.Response(400, json={"error": "pedido malformado"})

    with pytest.raises(RerankUnavailable):
        await reranker_com(respond).rerank(question="p", chunks=[chunk("c1")], top_k=1)

    assert len(tentativas) == 1


async def test_200_sem_a_chave_data_vira_RerankUnavailable() -> None:
    """O decorador do retriever só captura `RerankUnavailable`: um `KeyError` cru
    subiria até a borda e o chat daria erro em vez de cair para a ordem fundida."""
    tentativas: list[int] = []

    def respond(request: httpx.Request) -> httpx.Response:
        tentativas.append(1)
        return httpx.Response(200, json={"object": "list", "usage": {}})

    with pytest.raises(RerankUnavailable):
        await reranker_com(respond).rerank(question="p", chunks=[chunk("c1")], top_k=1)


async def test_item_sem_relevance_score_vira_RerankUnavailable() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0}], "usage": {}})

    with pytest.raises(RerankUnavailable):
        await reranker_com(respond).rerank(question="p", chunks=[chunk("c1")], top_k=1)


async def test_corta_em_top_k_mesmo_quando_o_provedor_devolve_mais() -> None:
    """Quem garante o tamanho é o cliente, não a promessa do provedor.

    `test_nunca_devolve_mais_que_o_top_k` passa porque o MockTransport honra `top_k` —
    prende o duplo, não o cliente. Se a Voyage devolvesse a lista inteira, a coluna
    reordenada do harness seria pontuada sobre uma lista mais longa que a fundida
    (acerto na posição 15 valendo 1/15 em vez de `None`) e o A/B favoreceria o rerank
    sozinho.
    """

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [{"index": i, "relevance_score": 1.0 - i / 10} for i in range(5)],
                "usage": {},
            },
        )

    ordenados = await reranker_com(respond).rerank(
        question="p", chunks=[chunk(f"c{i}") for i in range(5)], top_k=2
    )

    assert [c.chunk_id for c in ordenados] == ["c0", "c1"]


async def test_indice_repetido_nao_duplica_o_chunk() -> None:
    """Índice repetido deslocaria as posições seguintes e contaria o mesmo acerto duas
    vezes. Fica o melhor score, e o resto da lista mantém o tamanho honesto."""

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 0, "relevance_score": 0.9},
                    {"index": 0, "relevance_score": 0.8},
                    {"index": 1, "relevance_score": 0.5},
                ],
                "usage": {},
            },
        )

    ordenados = await reranker_com(respond).rerank(
        question="p", chunks=[chunk("c0"), chunk("c1")], top_k=3
    )

    assert [c.chunk_id for c in ordenados] == ["c0", "c1"]
    assert ordenados[0].rerank_score == 0.9

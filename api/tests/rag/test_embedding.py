"""O fake precisa ser determinístico para o pipeline inteiro rodar sem gastar API.
O cliente Voyage é testado contra um transporte falso — nenhum teste toca a rede.
"""

from collections.abc import Callable

import httpx
import pytest

from sensus_rag.embedding.fake import FakeEmbedder
from sensus_rag.embedding.voyage import (
    CHARS_PER_TOKEN,
    MAX_BATCH_TOKENS,
    VoyageEmbedder,
)
from sensus_rag.errors import EmbeddingUnavailable
from sensus_rag.http_retry import ATTEMPTS


async def test_fake_e_deterministico() -> None:
    embedder = FakeEmbedder(dimensions=8)

    primeiro = await embedder.embed_query("mesma pergunta")
    segundo = await embedder.embed_query("mesma pergunta")

    assert primeiro == segundo
    assert len(primeiro) == 8


async def test_fake_separa_textos_diferentes() -> None:
    embedder = FakeEmbedder(dimensions=8)

    assert await embedder.embed_query("um") != await embedder.embed_query("outro")


async def test_fake_normaliza_para_norma_unitaria() -> None:
    embedder = FakeEmbedder(dimensions=8)

    vector = await embedder.embed_query("qualquer coisa")

    assert sum(v * v for v in vector) == pytest.approx(1.0)


async def test_voyage_envia_o_input_type_de_documento_e_devolve_na_ordem() -> None:
    captured: dict[str, object] = {}

    def respond(request: httpx.Request) -> httpx.Response:
        import json

        captured.update(json.loads(request.content))
        assert request.headers["authorization"] == "Bearer chave-de-teste"
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {"object": "embedding", "embedding": [0.0, 1.0], "index": 1},
                    {"object": "embedding", "embedding": [1.0, 0.0], "index": 0},
                ],
                "model": "voyage-4-large",
                "usage": {"total_tokens": 4},
            },
        )

    transport = httpx.MockTransport(respond)
    async with httpx.AsyncClient(transport=transport) as http:
        embedder = VoyageEmbedder(
            api_key="chave-de-teste", model="voyage-4-large", dimensions=2, client=http
        )
        vectors = await embedder.embed_documents(["um", "dois"])

    assert captured["input_type"] == "document"
    assert captured["model"] == "voyage-4-large"
    assert captured["output_dimension"] == 2
    # A resposta veio fora de ordem de propósito: o índice manda, não a posição.
    assert vectors == [(1.0, 0.0), (0.0, 1.0)]


async def test_voyage_usa_input_type_de_consulta_na_pergunta() -> None:
    captured: dict[str, object] = {}

    def respond(request: httpx.Request) -> httpx.Response:
        import json

        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [{"object": "embedding", "embedding": [1.0, 0.0], "index": 0}],
                "model": "voyage-4-large",
                "usage": {"total_tokens": 2},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        embedder = VoyageEmbedder(
            api_key="chave", model="voyage-4-large", dimensions=2, client=http
        )
        await embedder.embed_query("uma pergunta")

    assert captured["input_type"] == "query"


async def test_voyage_quebra_em_lotes_de_no_maximo_mil() -> None:
    calls: list[int] = []

    def respond(request: httpx.Request) -> httpx.Response:
        import json

        inputs = json.loads(request.content)["input"]
        calls.append(len(inputs))
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {"object": "embedding", "embedding": [1.0, 0.0], "index": i}
                    for i in range(len(inputs))
                ],
                "model": "voyage-4-large",
                "usage": {"total_tokens": 1},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        embedder = VoyageEmbedder(
            api_key="chave", model="voyage-4-large", dimensions=2, client=http
        )
        vectors = await embedder.embed_documents(["t"] * 1500)

    assert calls == [1000, 500]
    assert len(vectors) == 1500


async def test_voyage_traduz_falha_para_embedding_unavailable() -> None:
    calls: list[int] = []
    pausas: list[float] = []
    respond = responder_com(500, calls)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        with pytest.raises(EmbeddingUnavailable):
            await embedder_sem_espera(http, pausas).embed_query("pergunta")

    # 5xx é falha do provedor: retenta até o teto antes de desistir.
    assert len(calls) == ATTEMPTS


async def test_voyage_nao_chama_a_api_com_lista_vazia() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        raise AssertionError("não deveria haver requisição")

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        embedder = VoyageEmbedder(
            api_key="chave", model="voyage-4-large", dimensions=2, client=http
        )

        assert await embedder.embed_documents([]) == []


def responder_com(
    status: int, calls: list[int], headers: dict[str, str] | None = None
) -> Callable[[httpx.Request], httpx.Response]:
    """Responde sempre com o mesmo status, contando as chamadas."""

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(status)
        return httpx.Response(status, text="detalhe do provedor", headers=headers or {})

    return respond


def embedder_sem_espera(http: httpx.AsyncClient, pausas: list[float]) -> VoyageEmbedder:
    """Embedder que registra quanto ESPERARIA, sem esperar de fato."""

    async def fake_sleep(seconds: float) -> None:
        pausas.append(seconds)

    return VoyageEmbedder(
        api_key="chave",
        model="voyage-4-large",
        dimensions=2,
        client=http,
        sleep=fake_sleep,
    )


async def test_voyage_retenta_quando_leva_429() -> None:
    """429 é o erro que MAIS merece retentativa — é o provedor dizendo "espere".

    Tratá-lo como 4xx definitivo faz uma cota momentaneamente estourada virar falha
    permanente da ingestão. Foi o que impediu a primeira rodada do harness de medição.
    """
    calls: list[int] = []
    pausas: list[float] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, text="rate limit")
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [{"object": "embedding", "embedding": [1.0, 0.0], "index": 0}],
                "model": "voyage-4-large",
                "usage": {"total_tokens": 2},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        vector = await embedder_sem_espera(http, pausas).embed_query("pergunta")

    assert vector == (1.0, 0.0)
    assert len(calls) == 2


async def test_voyage_respeita_o_retry_after_do_provedor() -> None:
    calls: list[int] = []
    pausas: list[float] = []
    respond = responder_com(429, calls, {"Retry-After": "30"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        with pytest.raises(EmbeddingUnavailable):
            await embedder_sem_espera(http, pausas).embed_query("pergunta")

    assert pausas and all(p == 30.0 for p in pausas)


async def test_voyage_espera_a_janela_do_minuto_quando_nao_ha_retry_after() -> None:
    # A janela de rate limit da Voyage é por minuto: esperar 1s ou 2s não devolve cota
    # nenhuma, só queima tentativa.
    calls: list[int] = []
    pausas: list[float] = []

    async with httpx.AsyncClient(transport=httpx.MockTransport(responder_com(429, calls))) as http:
        with pytest.raises(EmbeddingUnavailable):
            await embedder_sem_espera(http, pausas).embed_query("pergunta")

    assert pausas
    assert min(pausas) >= 20.0


async def test_voyage_desiste_de_429_depois_do_teto_de_tentativas() -> None:
    calls: list[int] = []
    pausas: list[float] = []

    async with httpx.AsyncClient(transport=httpx.MockTransport(responder_com(429, calls))) as http:
        with pytest.raises(EmbeddingUnavailable):
            await embedder_sem_espera(http, pausas).embed_query("pergunta")

    assert len(calls) == ATTEMPTS


async def test_voyage_quebra_o_lote_pelo_teto_de_tokens() -> None:
    """Contar itens não basta: a Voyage limita TOKENS por requisição.

    Um lote de 71 chunks de manual cabe folgado em 1000 itens e mesmo assim estoura a
    cota — foi exatamente assim que a primeira rodada do harness falhou.
    """
    tamanhos: list[int] = []

    def respond(request: httpx.Request) -> httpx.Response:
        import json

        inputs = json.loads(request.content)["input"]
        tamanhos.append(len(inputs))
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {"object": "embedding", "embedding": [1.0, 0.0], "index": i}
                    for i in range(len(inputs))
                ],
                "model": "voyage-4-large",
                "usage": {"total_tokens": 1},
            },
        )

    # Cada texto sozinho já é um quarto do teto: só cabem quatro por requisição.
    texto = "x" * (MAX_BATCH_TOKENS * CHARS_PER_TOKEN // 4)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        embedder = VoyageEmbedder(
            api_key="chave", model="voyage-4-large", dimensions=2, client=http
        )
        vectors = await embedder.embed_documents([texto] * 10)

    assert len(vectors) == 10
    assert max(tamanhos) <= 4
    assert sum(tamanhos) == 10


async def test_voyage_nao_retenta_erro_4xx() -> None:
    calls: list[int] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(401, text="Unauthorized")

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        embedder = VoyageEmbedder(
            api_key="chave-invalida", model="voyage-4-large", dimensions=2, client=http
        )
        with pytest.raises(EmbeddingUnavailable):
            await embedder.embed_query("pergunta")

    # Exatamente uma requisição, sem retentativas.
    assert len(calls) == 1

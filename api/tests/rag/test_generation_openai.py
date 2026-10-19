"""Cliente da OpenAI contra transporte falso.

O `MockTransport` serve SSE gravado, e o parsing exercitado é o de produção — nada
entre o teste e o código é dublado. É a resposta à lição da fatia 2a: duplo que
substitui o componente real esconde o defeito em vez de expô-lo.
"""

import json
from collections.abc import AsyncIterator

import httpx
import pytest

from sensus_rag.contract import (
    AnswerRefusal,
    AnswerToken,
    GenerationConfig,
    RetrievedChunk,
)
from sensus_rag.errors import GenerationTimeout, GenerationUnavailable
from sensus_rag.generation.openai import OpenAIAnswerGenerator
from sensus_rag.generation.prompt import SENTINEL

CHUNK = RetrievedChunk(
    chunk_id="c1",
    document_id="d1",
    document_name="cdc.md",
    content="o prazo é de sete dias",
    score=0.5,
)


def sse(*texts: str) -> bytes:
    lines = [
        b'data: {"choices":[{"delta":{"content":' + f'"{t}"'.encode() + b"}}]}\n\n" for t in texts
    ]
    return b"".join(lines) + b"data: [DONE]\n\n"


def generator_over(handler: httpx.MockTransport, **kwargs: object) -> OpenAIAnswerGenerator:
    """O gerador não guarda mais modelo nem teto: desde a fatia 4a eles vêm no `config`
    de cada requisição, porque uma instância só serve todos os tenants."""
    return OpenAIAnswerGenerator(
        api_key="chave-de-teste",
        client=httpx.AsyncClient(transport=handler),
        **kwargs,  # type: ignore[arg-type]
    )


CONFIG = GenerationConfig(model="gpt-4o-mini")


async def run(generator: OpenAIAnswerGenerator) -> list[object]:
    return [
        e async for e in generator.generate(question="Qual o prazo?", chunks=[CHUNK], config=CONFIG)
    ]


async def test_transmite_os_tokens_da_resposta() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=sse("O prazo ", "é de sete dias."))
    )

    events = await run(generator_over(transport))

    assert events == [AnswerToken("O prazo "), AnswerToken("é de sete dias.")]


async def test_evento_partido_entre_pedacos_da_rede_e_remontado() -> None:
    payload = sse("resposta inteira")
    metade = len(payload) // 2

    async def partido() -> AsyncIterator[bytes]:
        yield payload[:metade]
        yield payload[metade:]

    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=partido()))

    events = await run(generator_over(transport))

    assert events == [AnswerToken("resposta inteira")]


async def test_sentinela_do_provedor_vira_recusa() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=sse(SENTINEL)))

    assert await run(generator_over(transport)) == [AnswerRefusal()]


async def test_manda_o_modelo_a_chave_e_o_streaming() -> None:
    visto: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        visto["auth"] = request.headers["authorization"]
        visto["body"] = json.loads(request.content)
        return httpx.Response(200, content=sse("ok"))

    await run(generator_over(httpx.MockTransport(handler)))

    body = visto["body"]
    assert isinstance(body, dict)
    assert visto["auth"] == "Bearer chave-de-teste"
    assert body["model"] == "gpt-4o-mini"
    assert body["stream"] is True
    assert body["temperature"] == 0


async def test_429_e_retentado_antes_do_primeiro_token() -> None:
    tentativas = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal tentativas
        tentativas += 1
        if tentativas == 1:
            return httpx.Response(429, content=b"devagar")
        return httpx.Response(200, content=sse("depois da espera"))

    dormidas: list[float] = []

    async def registra(seconds: float) -> None:
        dormidas.append(seconds)

    events = await run(generator_over(httpx.MockTransport(handler), sleep=registra))

    assert tentativas == 2
    assert events == [AnswerToken("depois da espera")]
    assert dormidas, "o cliente tem de ESPERAR antes de retentar, não retentar na hora"


async def test_429_com_retry_after_dorme_o_valor_pedido_pelo_provedor() -> None:
    tentativas = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal tentativas
        tentativas += 1
        if tentativas == 1:
            return httpx.Response(429, headers={"retry-after": "5"}, content=b"devagar")
        return httpx.Response(200, content=sse("depois da espera"))

    dormidas: list[float] = []

    async def registra(seconds: float) -> None:
        dormidas.append(seconds)

    events = await run(generator_over(httpx.MockTransport(handler), sleep=registra))

    assert tentativas == 2
    assert events == [AnswerToken("depois da espera")]
    assert dormidas == [5.0], "o Retry-After do provedor tem de virar a pausa exata"


async def test_pausas_sem_retry_after_escalonam_entre_tentativas() -> None:
    tentativas = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal tentativas
        tentativas += 1
        if tentativas < 3:
            return httpx.Response(429, content=b"devagar")
        return httpx.Response(200, content=sse("na terceira"))

    dormidas: list[float] = []

    async def registra(seconds: float) -> None:
        dormidas.append(seconds)

    events = await run(generator_over(httpx.MockTransport(handler), sleep=registra))

    assert tentativas == 3
    assert events == [AnswerToken("na terceira")]
    assert len(dormidas) == 2
    assert dormidas[0] != dormidas[1]
    assert dormidas[1] > dormidas[0], "a segunda pausa tem de ser maior que a primeira"


async def test_400_nao_e_retentado() -> None:
    tentativas = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal tentativas
        tentativas += 1
        return httpx.Response(400, content=b"modelo inexistente")

    with pytest.raises(GenerationUnavailable):
        await run(generator_over(httpx.MockTransport(handler)))

    assert tentativas == 1


async def test_timeout_vira_generation_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("demorou", request=request)

    with pytest.raises(GenerationTimeout):
        await run(generator_over(httpx.MockTransport(handler)))


async def test_resposta_ilegivel_vira_generation_unavailable() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=b"data: nao-e-json\n\n")
    )

    with pytest.raises(GenerationUnavailable):
        await run(generator_over(transport))


async def test_falha_depois_do_primeiro_token_nao_retenta() -> None:
    tentativas = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal tentativas
        tentativas += 1
        return httpx.Response(
            200,
            content=b'data: {"choices":[{"delta":{"content":"ini"}}]}\n\ndata: quebrado\n\n',
        )

    with pytest.raises(GenerationUnavailable):
        await run(generator_over(httpx.MockTransport(handler)))

    # Retentar depois que o token saiu duplicaria texto na tela do usuário.
    assert tentativas == 1


# --- Config por requisição (fatia 4a) -----------------------------------------------


async def test_o_modelo_vem_do_config_da_requisicao_e_nao_do_objeto() -> None:
    """O gerador é montado uma vez no `build_rag` e serve todos os tenants. Guardar o
    modelo nele faria a config por tenant não ter como chegar — e o sintoma seria a
    config salva sem efeito nenhum, sem erro."""
    visto: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        visto.update(json.loads(request.content))
        return httpx.Response(200, content=sse("ok"))

    generator = generator_over(httpx.MockTransport(handler))
    _ = [
        e
        async for e in generator.generate(
            question="p?", chunks=[CHUNK], config=GenerationConfig(model="modelo-do-tenant")
        )
    ]

    assert visto["model"] == "modelo-do-tenant"


async def test_o_briefing_do_config_chega_ao_system_prompt() -> None:
    visto: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        visto.update(json.loads(request.content))
        return httpx.Response(200, content=sse("ok"))

    generator = generator_over(httpx.MockTransport(handler))
    _ = [
        e
        async for e in generator.generate(
            question="p?",
            chunks=[CHUNK],
            config=GenerationConfig(model="m", briefing="Fale como um advogado."),
        )
    ]

    mensagens = visto["messages"]
    assert isinstance(mensagens, list)
    assert "Fale como um advogado." in mensagens[0]["content"]


async def test_o_teto_de_contexto_vem_do_config() -> None:
    """Dois tenants com tetos diferentes na mesma instância do gerador."""
    visto: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        visto.update(json.loads(request.content))
        return httpx.Response(200, content=sse("ok"))

    generator = generator_over(httpx.MockTransport(handler))
    longo = RetrievedChunk(
        chunk_id="c1",
        document_id="d1",
        document_name="doc.md",
        content="x" * 500,
        score=0.9,
    )
    _ = [
        e
        async for e in generator.generate(
            question="p?",
            chunks=[longo],
            config=GenerationConfig(model="m", max_context_chars=100),
        )
    ]

    mensagens = visto["messages"]
    assert isinstance(mensagens, list)
    assert mensagens[1]["content"].count("x") == 100

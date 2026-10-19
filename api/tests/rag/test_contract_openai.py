"""Teste de contrato: o modelo real obedece ao sentinela?

Pulado por padrão. Roda com:
    uv run pytest -m contract

A chave e o modelo vêm das settings (fixtures de `tests/conftest.py`), não de `os.environ`:
lendo do ambiente este teste PULAVA numa máquina configurada por `.env`.

Todo o desenho da fatia 2b se apoia nesta obediência — sem este teste ela é suposição.
É o que impede o duplo de divergir em silêncio, que é o risco real de desenvolver
contra duplo. Gasta duas chamadas do provedor por execução.
"""

import httpx
import pytest

from sensus_rag.contract import AnswerRefusal, AnswerToken, GenerationConfig, RetrievedChunk
from sensus_rag.generation.openai import OpenAIAnswerGenerator

pytestmark = pytest.mark.contract

RELEVANTE = RetrievedChunk(
    chunk_id="c1",
    document_id="d1",
    document_name="politica.md",
    content=("O prazo para solicitar reembolso é de 30 dias corridos, contados da data da compra."),
    score=0.5,
)

IRRELEVANTE = RetrievedChunk(
    chunk_id="c2",
    document_id="d2",
    document_name="cardapio.md",
    content="A sopa do dia é de abóbora e vem acompanhada de pão de fermentação natural.",
    score=0.5,
)


def build(
    api_key: str, model: str
) -> tuple[OpenAIAnswerGenerator, httpx.AsyncClient, GenerationConfig]:
    client = httpx.AsyncClient(timeout=60.0)
    generator = OpenAIAnswerGenerator(api_key=api_key, client=client)
    return generator, client, GenerationConfig(model=model)


async def test_trecho_que_responde_produz_tokens(
    openai_api_key: str, generation_model: str
) -> None:
    generator, client, config = build(openai_api_key, generation_model)
    async with client:
        events = [
            e
            async for e in generator.generate(
                question="Qual o prazo para pedir reembolso?",
                chunks=[RELEVANTE],
                config=config,
            )
        ]

    assert events, "nenhum evento recebido do provedor"
    assert all(isinstance(e, AnswerToken) for e in events)
    assert "30" in "".join(e.text for e in events if isinstance(e, AnswerToken))


async def test_trecho_irrelevante_produz_recusa(openai_api_key: str, generation_model: str) -> None:
    """O caso que a fatia inteira existe para acertar: o modelo obedece ao sentinela
    em vez de responder de memória."""
    generator, client, config = build(openai_api_key, generation_model)
    async with client:
        events = [
            e
            async for e in generator.generate(
                question="Qual o prazo para pedir reembolso?",
                chunks=[IRRELEVANTE],
                config=config,
            )
        ]

    assert events == [AnswerRefusal()]

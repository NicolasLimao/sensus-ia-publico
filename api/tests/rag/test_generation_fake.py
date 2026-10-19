"""O duplo da geração emite o roteiro e satisfaz o Protocol."""

import pytest

from sensus_rag.contract import (
    AnswerGenerator,
    AnswerRefusal,
    AnswerToken,
    RetrievedChunk,
)
from sensus_rag.errors import GenerationUnavailable
from sensus_rag.generation.fake import CONFIG_DE_DUPLO, ScriptedGenerator

CHUNK = RetrievedChunk(
    chunk_id="c1",
    document_id="d1",
    document_name="cdc.md",
    content="conteúdo",
    score=0.5,
)


async def test_emite_o_roteiro_na_ordem() -> None:
    generator: AnswerGenerator = ScriptedGenerator([AnswerToken("Uma "), AnswerToken("resposta.")])

    events = [
        e async for e in generator.generate(question="p?", chunks=[CHUNK], config=CONFIG_DE_DUPLO)
    ]

    assert events == [AnswerToken("Uma "), AnswerToken("resposta.")]


async def test_registra_a_chamada() -> None:
    generator = ScriptedGenerator([AnswerRefusal()])

    _ = [e async for e in generator.generate(question="p?", chunks=[CHUNK], config=CONFIG_DE_DUPLO)]

    pergunta, quantos, config = generator.calls[0]
    assert (pergunta, quantos) == ("p?", 1)
    # O config entrou na fatia 4a: o duplo guarda o que recebeu, e é assim que o
    # teste do adaptador prova que a config do tenant chegou até aqui.
    assert config.model == "duplo"


async def test_estoura_depois_do_roteiro() -> None:
    generator = ScriptedGenerator(
        [AnswerToken("parcial")], error=GenerationUnavailable("provedor caiu")
    )

    with pytest.raises(GenerationUnavailable):
        _ = [
            e
            async for e in generator.generate(question="p?", chunks=[CHUNK], config=CONFIG_DE_DUPLO)
        ]

import pytest

from sensus_api.errors import code_for
from sensus_api.upstream.contract import (
    Chunk,
    ResultEvent,
    StageEvent,
    TokenEvent,
    UpstreamBadRequest,
    UpstreamTimeout,
)
from sensus_api.upstream.fake import ScriptedUpstream


async def test_duplo_emite_o_roteiro_e_registra_a_chamada() -> None:
    upstream = ScriptedUpstream(
        [
            StageEvent("retrieving"),
            TokenEvent("Olá"),
            ResultEvent(chunks=(Chunk(id="8231", excerpt="trecho", score=0.31),), score=0.31),
        ]
    )

    received = [
        event async for event in upstream.ask(question="pergunta?", user_id="u1", tenant_id="t1")
    ]

    assert received == upstream.events
    assert upstream.calls == [("pergunta?", "u1", "t1")]


async def test_duplo_estoura_depois_de_emitir_o_roteiro() -> None:
    upstream = ScriptedUpstream([TokenEvent("parcial")], error=UpstreamTimeout("estourou"))

    with pytest.raises(UpstreamTimeout):
        async for _ in upstream.ask(question="q", user_id="u", tenant_id="t"):
            pass


def test_excecoes_viram_codigos_do_sensus() -> None:
    assert code_for(UpstreamTimeout("x"))[0] == "upstream_timeout"
    assert code_for(UpstreamBadRequest("x"))[0] == "bad_request"
    assert code_for(RuntimeError("qualquer coisa"))[0] == "service_unavailable"

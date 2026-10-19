import json

from sensus_api.store import InMemoryConversationStore
from sensus_api.translate import stream_chat
from sensus_api.upstream.contract import (
    Chunk,
    ResultEvent,
    StageEvent,
    TokenEvent,
    UpstreamTimeout,
)
from sensus_api.upstream.fake import ScriptedUpstream


def parse(blocks: list[str]) -> list[tuple[str, dict[str, object]]]:
    """Transforma blocos SSE crus em (evento, payload) para facilitar a asserção."""
    parsed: list[tuple[str, dict[str, object]]] = []
    for block in blocks:
        lines = block.strip().split("\n")
        name = lines[0].removeprefix("event: ")
        payload = json.loads(lines[1].removeprefix("data: "))
        parsed.append((name, payload))
    return parsed


async def collect(
    client: ScriptedUpstream, store: InMemoryConversationStore
) -> list[tuple[str, dict[str, object]]]:
    blocks = [
        block
        async for block in stream_chat(
            client, question="pergunta?", user_id="u1", tenant_id="t1", store=store
        )
    ]
    return parse(blocks)


async def test_resposta_fundamentada_vira_a_sequencia_completa_de_eventos() -> None:
    chunk = Chunk(id="8231", excerpt="trecho relevante", score=0.31)
    client = ScriptedUpstream(
        [
            StageEvent("retrieving"),
            StageEvent("generating"),
            TokenEvent("Uma "),
            TokenEvent("resposta."),
            ResultEvent(chunks=(chunk,), score=0.31),
        ]
    )
    store = InMemoryConversationStore()

    events = await collect(client, store)

    names = [name for name, _ in events]
    assert names == ["stage", "stage", "token", "token", "sources", "done"]
    assert events[0][1] == {"stage": "retrieving"}
    assert events[2][1] == {"text": "Uma "}
    assert events[4][1] == {
        "sources": [{"id": "8231", "excerpt": "trecho relevante", "document": None, "score": 0.31}]
    }
    done = events[5][1]
    assert done["chunks_used"] == 1
    assert done["score"] == 0.31
    assert isinstance(done["message_id"], str) and done["message_id"]
    assert isinstance(done["elapsed_ms"], int)


async def test_resposta_sem_fundamento_e_um_done_normal_e_nao_um_erro() -> None:
    client = ScriptedUpstream(
        [
            TokenEvent("Não encontrei isso na base."),
            ResultEvent(chunks=(), score=None),
        ]
    )
    store = InMemoryConversationStore()

    events = await collect(client, store)

    names = [name for name, _ in events]
    assert "error" not in names
    assert names[-1] == "done"
    assert events[-1][1]["chunks_used"] == 0
    assert ("sources", {"sources": []}) in events


async def test_falha_do_upstream_vira_evento_error_com_codigo_do_sensus() -> None:
    client = ScriptedUpstream(
        [TokenEvent("parcial")], error=UpstreamTimeout("ReadTimeout na tabela interna")
    )
    store = InMemoryConversationStore()

    events = await collect(client, store)

    name, payload = events[-1]
    assert name == "error"
    assert payload["code"] == "upstream_timeout"
    # A mensagem do upstream não pode vazar para o navegador.
    assert "tabela" not in str(payload["message"])


async def test_pergunta_e_resposta_ficam_no_historico_com_as_fontes() -> None:
    chunk = Chunk(id="8231", excerpt="trecho", score=0.31)
    client = ScriptedUpstream([TokenEvent("Resposta."), ResultEvent(chunks=(chunk,), score=0.31)])
    store = InMemoryConversationStore()

    events = await collect(client, store)

    history = await store.history("u1")
    assert [m.role for m in history] == ["user", "assistant"]
    assert history[0].content == "pergunta?"
    assert history[1].content == "Resposta."
    assert history[1].sources == (chunk,)
    assert history[1].id == events[-1][1]["message_id"]


async def test_stream_interrompido_nao_entra_no_historico() -> None:
    client = ScriptedUpstream([TokenEvent("parcial")], error=UpstreamTimeout("x"))
    store = InMemoryConversationStore()

    await collect(client, store)

    history = await store.history("u1")
    assert [m.role for m in history] == ["user"]

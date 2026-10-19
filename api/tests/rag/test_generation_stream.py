"""Detector de recusa. Cada caso aqui é um jeito conhecido de estragar o streaming."""

from collections.abc import AsyncIterator, Sequence

from sensus_rag.contract import AnswerRefusal, AnswerToken, GenerationEvent
from sensus_rag.generation.prompt import SENTINEL
from sensus_rag.generation.stream import classify


async def deltas(pieces: Sequence[str]) -> AsyncIterator[str]:
    for piece in pieces:
        yield piece


async def collect(pieces: Sequence[str]) -> list[GenerationEvent]:
    return [event async for event in classify(deltas(pieces))]


async def test_sentinela_no_inicio_vira_recusa_sem_nenhum_token() -> None:
    events = await collect([SENTINEL])

    assert events == [AnswerRefusal()]


async def test_sentinela_partido_entre_deltas_ainda_e_recusa() -> None:
    events = await collect(["[SEM_", "FUNDAMENTO", "]"])

    assert events == [AnswerRefusal()]


async def test_sentinela_no_meio_da_resposta_nao_e_recusa() -> None:
    events = await collect(["A lei ", f"cita {SENTINEL} como exemplo."])

    assert not any(isinstance(e, AnswerRefusal) for e in events)
    assert "".join(e.text for e in events if isinstance(e, AnswerToken)) == (
        f"A lei cita {SENTINEL} como exemplo."
    )


async def test_resposta_normal_nao_perde_o_comeco() -> None:
    events = await collect(["Uma ", "resposta ", "fundamentada."])

    assert "".join(e.text for e in events if isinstance(e, AnswerToken)) == (
        "Uma resposta fundamentada."
    )


async def test_stream_curto_demais_para_decidir_ainda_entrega_o_texto() -> None:
    # "[SEM" é prefixo do sentinela: o detector segura, o stream acaba, e o que foi
    # segurado tem de sair — engolir seria perder resposta.
    events = await collect(["[SEM"])

    assert events == [AnswerToken("[SEM")]


async def test_stream_vazio_nao_emite_nada() -> None:
    assert await collect([]) == []


async def test_stream_que_comeca_com_espaco_preserva_o_espaco_inicial() -> None:
    # Espaço em branco não sentineliza: só o `lstrip()` interno decide se ainda pode
    # virar o sentinela. O token final tem de sair com o espaço, sem perdê-lo.
    events = await collect(["  ", "Uma resposta."])

    assert events == [AnswerToken("  Uma resposta.")]


async def test_quase_prefixo_que_diverge_no_meio_sai_como_token_sem_segurar_demais() -> None:
    # "[SEMX" diverge do sentinela no 5º caractere ('X' no lugar de '_'): não é mais
    # candidato a sentinela, então tem de sair como token assim que chega — sem
    # segurar à espera de mais deltas que nunca vão desfazer a divergência.
    events = await collect(["[SEMX", " resto"])

    assert events == [AnswerToken("[SEMX"), AnswerToken(" resto")]

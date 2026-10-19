"""Transforma os pedaços de texto do provedor em eventos de domínio.

Separado do cliente HTTP de propósito: a decisão "isto é recusa?" é a peça mais sutil
da fatia — se ela segurar demais, a primeira palavra de toda resposta some; se soltar
cedo demais, o usuário vê o sentinela na tela. Pura, ela é testável sem rede.

A decisão é pelo INÍCIO da saída: sentinela no meio de uma resposta legítima não é
recusa.
"""

from collections.abc import AsyncIterator

from sensus_rag.contract import AnswerRefusal, AnswerToken, GenerationEvent
from sensus_rag.generation.prompt import SENTINEL


async def classify(deltas: AsyncIterator[str]) -> AsyncIterator[GenerationEvent]:
    """Detecta se o stream começa com o sentinela de recusa e classifica os tokens.

    Retorna um AsyncIterator que emite AnswerRefusal se o stream começar com o
    sentinela, ou AnswerToken para cada pedaço de texto do stream caso contrário.
    """
    buffer = ""
    decided = False

    async for delta in deltas:
        if decided:
            yield AnswerToken(delta)
            continue

        buffer += delta
        stripped = buffer.lstrip()

        if len(stripped) < len(SENTINEL):
            if SENTINEL.startswith(stripped):
                # Ainda pode virar o sentinela: segura mais um pedaço.
                continue
        elif stripped.startswith(SENTINEL):
            yield AnswerRefusal()
            return

        decided = True
        # O buffer sai inteiro e sem strip: espaço do começo é do texto, não nosso.
        yield AnswerToken(buffer)
        buffer = ""

    if not decided and buffer:
        # O stream acabou enquanto o detector ainda segurava. Nada se perde.
        yield AnswerToken(buffer)

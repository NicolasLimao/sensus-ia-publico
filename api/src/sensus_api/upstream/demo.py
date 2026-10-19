"""Upstream de demonstração.

Existe para que a fatia rode ponta a ponta antes de haver upstream real: a interface
inteira — streaming, estágios, citação, feedback, restauração — fica exercitável sem
depender de outro repositório.

O conteúdo é genérico e escrito aqui. Nenhum dado, prompt ou documento de cliente.
"""

import asyncio
from collections.abc import AsyncIterator

from sensus_api.upstream.contract import (
    Chunk,
    ResultEvent,
    Stage,
    StageEvent,
    TokenEvent,
    UpstreamEvent,
)

_CHUNKS = (
    Chunk(
        id="demo-1",
        excerpt=(
            "Este é um trecho de demonstração. No produto, aqui apareceria a passagem "
            "exata do documento do cliente que fundamentou a resposta."
        ),
        score=0.42,
    ),
    Chunk(
        id="demo-2",
        excerpt=(
            "Segundo trecho de demonstração. A citação é por trecho, e não por nome de "
            "documento, porque é o trecho que o sistema realmente usou."
        ),
        score=0.31,
    ),
)


class DemoUpstream:
    """Responde com um texto fixo, em streaming, como se fosse o pipeline real."""

    def __init__(self, delay: float = 0.04) -> None:
        self.delay = delay

    async def ask(
        self, *, question: str, user_id: str, tenant_id: str
    ) -> AsyncIterator[UpstreamEvent]:
        stages: tuple[Stage, ...] = ("retrieving", "reranking", "generating")
        for stage in stages:
            await self._pause()
            yield StageEvent(stage)

        answer = (
            f'Você perguntou: "{question}".\n\n'
            "Esta resposta vem do **upstream de demonstração**, não de uma base real — "
            "ela existe para exercitar a interface ponta a ponta: streaming, estágios da "
            "espera, citação por trecho e feedback.\n\n"
            "Quando o pipeline real for ligado, o que muda é só de onde o texto vem."
        )
        for word in answer.split(" "):
            await self._pause()
            yield TokenEvent(word + " ")

        yield ResultEvent(chunks=_CHUNKS, score=0.37)

    async def _pause(self) -> None:
        if self.delay:
            await asyncio.sleep(self.delay)

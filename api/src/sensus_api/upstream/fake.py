"""Duplo do upstream: emite um roteiro fixo de eventos e, opcionalmente, estoura.

Um único duplo cobre os dois casos exigidos pelo desenho — o que emite tokens e o que
estoura — porque estourar é só um roteiro que termina em exceção.
"""

import asyncio
from collections.abc import AsyncIterator, Sequence

from sensus_api.upstream.contract import UpstreamError, UpstreamEvent

AskCall = tuple[str, str, str]


class ScriptedUpstream:
    def __init__(
        self,
        events: Sequence[UpstreamEvent],
        *,
        error: UpstreamError | None = None,
        delay: float = 0.0,
    ) -> None:
        self.events = list(events)
        self.error = error
        self.delay = delay
        self.calls: list[AskCall] = []

    async def ask(
        self, *, question: str, user_id: str, tenant_id: str
    ) -> AsyncIterator[UpstreamEvent]:
        self.calls.append((question, user_id, tenant_id))
        for event in self.events:
            if self.delay:
                await asyncio.sleep(self.delay)
            yield event
        if self.error is not None:
            raise self.error

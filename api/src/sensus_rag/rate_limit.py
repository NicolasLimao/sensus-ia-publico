"""Espaçamento de requisições a provedor externo (embeddings e geração).

Retentar depois do `429` conserta o sintoma; **não** conserta a causa. Numa cota apertada
a retentativa piora o quadro: a requisição recusada também conta contra o limite de
requisições por minuto, então tentar de novo cedo demais gasta a cota que faltava. A
conta gratuita da Voyage, sem método de pagamento, fica em 3 RPM e 10K TPM — nela o
pipeline se debate sem sair do lugar.

A saída é o contrário da retentativa: **esperar antes**, não depois. Este limitador
segura a requisição até ela caber na janela, e as duas cotas — requisições e tokens —
são verificadas juntas, porque estourar qualquer uma derruba a chamada.

Janela deslizante e não balde de fichas: as cotas do provedor são "por minuto corrido",
que é exatamente o que a janela modela. O balde permitiria uma rajada inicial do tamanho
do balde, e é justamente a rajada que a cota recusa.
"""

import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

WINDOW_SECONDS = 60.0


async def _default_sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


@dataclass
class SlidingWindowLimiter:
    """Segura a requisição até ela caber nas cotas de requisições e de tokens.

    `sleep` e `now` são injetáveis para que o teste verifique a espera sem gastá-la:
    um limitador correto é feito de esperas longas, e um teste que as cumpre de verdade
    fica lento demais para rodar na suíte padrão.
    """

    max_requests: int
    max_tokens: int
    sleep: Callable[[float], Awaitable[None]] = _default_sleep
    now: Callable[[], float] = time.monotonic
    _history: deque[tuple[float, int]] = field(default_factory=deque, repr=False)

    async def acquire(self, tokens: int) -> None:
        """Volta quando a requisição pode sair — imediatamente ou depois da espera."""
        while True:
            current = self.now()
            self._forget_older_than(current)
            wait = self._wait_needed(tokens, current)
            if wait <= 0.0:
                self._history.append((current, tokens))
                return
            await self.sleep(wait)

    def _forget_older_than(self, current: float) -> None:
        while self._history and current - self._history[0][0] >= WINDOW_SECONDS:
            self._history.popleft()

    def _wait_needed(self, tokens: int, current: float) -> float:
        waits = [0.0]

        if self.max_requests > 0 and len(self._history) >= self.max_requests:
            waits.append(self._release_at(self._history[0][0], current))

        if self.max_tokens > 0:
            used = sum(amount for _, amount in self._history)
            excess = used + tokens - self.max_tokens
            if excess > 0:
                waits.append(self._wait_for_tokens(excess, current))

        return max(waits)

    def _wait_for_tokens(self, excess: int, current: float) -> float:
        """Espera até sair da janela requisição suficiente para o excesso caber."""
        freed = 0
        for stamp, amount in self._history:
            freed += amount
            if freed >= excess:
                return self._release_at(stamp, current)
        # Nem esvaziando a janela inteira cabe: a requisição sozinha passa do teto de
        # tokens. Esperar mais não resolve — quem evita isso é o teto de lote do
        # embedder, que deve ser menor que a cota. Espera a janela esvaziar e deixa
        # seguir, para falhar no provedor com erro claro em vez de travar aqui.
        if self._history:
            return self._release_at(self._history[-1][0], current)
        return 0.0

    def _release_at(self, stamp: float, current: float) -> float:
        return max(0.0, stamp + WINDOW_SECONDS - current)


def limiter_from(requests_per_minute: int, tokens_per_minute: int) -> SlidingWindowLimiter | None:
    """`None` quando as duas cotas estão zeradas — nesse caso não há o que espaçar, e
    um limitador inerte só adicionaria uma camada para o leitor atravessar."""
    if requests_per_minute <= 0 and tokens_per_minute <= 0:
        return None
    return SlidingWindowLimiter(max_requests=requests_per_minute, max_tokens=tokens_per_minute)

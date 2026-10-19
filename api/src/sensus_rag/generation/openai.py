"""Geração pela OpenAI, por HTTP.

HTTP direto em vez do SDK do provedor, pelo mesmo motivo do `VoyageEmbedder`: com
`httpx.MockTransport` o cliente inteiro é testável sem rede, e trocar de provedor passa
a ser trocar um arquivo. Adotar o SDK aqui criaria dois padrões de cliente no mesmo
pacote e uma dependência nova.

Retentativa **só antes do primeiro token**: depois que o texto começou a aparecer na
tela, repetir a chamada duplicaria a resposta — do ponto de vista do usuário ela não é
idempotente.
"""

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx

from sensus_rag.contract import GenerationConfig, GenerationEvent, RetrievedChunk
from sensus_rag.errors import GenerationTimeout, GenerationUnavailable
from sensus_rag.generation.prompt import build_prompt
from sensus_rag.generation.stream import classify
from sensus_rag.rate_limit import SlidingWindowLimiter

ENDPOINT = "https://api.openai.com/v1/chat/completions"

ATTEMPTS = 3
TOO_MANY_REQUESTS = 429

# A janela de cota é por MINUTO; esperar 1s não devolve cota, só queima tentativa.
RATE_LIMIT_PAUSE_SECONDS = 20.0
MAX_PAUSE_SECONDS = 120.0

DATA_PREFIX = "data:"
DONE = "[DONE]"


async def _default_sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


def _is_retryable(status: int) -> bool:
    """5xx é falha do provedor; 429 é o provedor pedindo para esperar. Os demais 4xx
    (modelo inexistente, chave inválida) não melhoram com repetição."""
    return status == TOO_MANY_REQUESTS or status >= 500


def _pause_for(response: httpx.Response, attempt: int) -> float:
    header = response.headers.get("retry-after", "").strip()
    if header:
        try:
            return min(float(header), MAX_PAUSE_SECONDS)
        except ValueError:
            # `Retry-After` também aceita data HTTP; aí vale o backoff padrão.
            pass
    if response.status_code == TOO_MANY_REQUESTS:
        return min(RATE_LIMIT_PAUSE_SECONDS * (attempt + 1), MAX_PAUSE_SECONDS)
    return float(2**attempt)


@dataclass(frozen=True)
class OpenAIAnswerGenerator:
    # `repr=False`: um `repr()` deste objeto (log, traceback) nunca deve imprimir a chave.
    api_key: str = field(repr=False)
    client: httpx.AsyncClient
    timeout_seconds: float = 60.0
    # Injetável para que o teste de retentativa não gaste o tempo que ele mede.
    sleep: Callable[[float], Awaitable[None]] = field(default=_default_sleep)
    # Sem limitador as requisições saem à vontade — certo para o chat, que faz uma
    # chamada por pergunta. Quem sente cota é o harness, que roda dezenas em série.
    limiter: SlidingWindowLimiter | None = None

    async def generate(
        self,
        *,
        question: str,
        chunks: Sequence[RetrievedChunk],
        config: GenerationConfig,
    ) -> AsyncIterator[GenerationEvent]:
        async for event in classify(self._deltas(question, chunks, config)):
            yield event

    async def _deltas(
        self, question: str, chunks: Sequence[RetrievedChunk], config: GenerationConfig
    ) -> AsyncIterator[str]:
        # Modelo, teto e briefing vêm do CONFIG, não do objeto: uma instância só serve
        # todos os tenants, e o que varia por tenant não pode morar nela.
        system, user = build_prompt(
            question=question,
            chunks=chunks,
            max_context_chars=config.max_context_chars,
            briefing=config.briefing,
        )
        body: dict[str, Any] = {
            "model": config.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": True,
            "temperature": 0,
        }
        last_error: Exception | None = None
        for attempt in range(ATTEMPTS):
            if self.limiter is not None:
                # O limitador de geração só espaça por REQUISIÇÃO (é montado com
                # `max_tokens=0`, ver `rag_wiring.py`): a contagem de tokens não é
                # usada, então não vale estimá-la aqui — diferente do `VoyageEmbedder`,
                # cuja cota é por tokens de verdade.
                await self.limiter.acquire(0)
            try:
                async for delta in self._attempt(body, attempt):
                    yield delta
                return
            except _Retryable as exc:
                last_error = exc.__cause__ if isinstance(exc.__cause__, Exception) else exc
                if attempt < ATTEMPTS - 1:
                    await self.sleep(exc.pause)

        # A mensagem do provedor pode carregar detalhe interno: fica no `raise from`,
        # que vai para o log, e nunca no texto que o navegador recebe.
        raise GenerationUnavailable("provedor de geração indisponível") from last_error

    async def _attempt(self, body: dict[str, Any], attempt: int) -> AsyncIterator[str]:
        """Uma tentativa. Levanta `_Retryable` só ANTES do primeiro token."""
        started = False
        try:
            async with self.client.stream(
                "POST",
                ENDPOINT,
                json=body,
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=self.timeout_seconds,
            ) as response:
                if response.status_code >= 400:
                    await response.aread()
                    try:
                        response.raise_for_status()
                    except httpx.HTTPStatusError as exc:
                        if _is_retryable(response.status_code):
                            raise _Retryable(_pause_for(response, attempt)) from exc
                        raise GenerationUnavailable("provedor de geração indisponível") from exc

                async for line in response.aiter_lines():
                    text = _content_of(line)
                    if text is None:
                        continue
                    if text == "":
                        # Delta sem conteúdo (papel, parada) — nada a transmitir.
                        continue
                    started = True
                    yield text
        except httpx.TimeoutException as exc:
            raise GenerationTimeout("provedor de geração não respondeu") from exc
        except httpx.HTTPError as exc:
            if started:
                raise GenerationUnavailable("provedor de geração indisponível") from exc
            raise _Retryable(float(2**attempt)) from exc


def _content_of(line: str) -> str | None:
    """Texto do delta, `""` para evento sem conteúdo, `None` para linha que não é dado.

    Levanta `GenerationUnavailable` para dado ilegível: uma linha `data:` que não é o
    JSON esperado significa que o contrato com o provedor mudou, e seguir em silêncio
    entregaria uma resposta truncada como se estivesse completa.
    """
    if not line.startswith(DATA_PREFIX):
        return None
    payload = line[len(DATA_PREFIX) :].strip()
    if not payload or payload == DONE:
        return None
    try:
        choices = json.loads(payload)["choices"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise GenerationUnavailable("resposta ilegível do provedor de geração") from exc
    if not choices:
        return ""
    delta = choices[0].get("delta") or {}
    content = delta.get("content")
    return "" if content is None else str(content)


class _Retryable(Exception):
    """Falha que ainda vale repetir, com a pausa que o provedor pediu."""

    def __init__(self, pause: float) -> None:
        super().__init__("retentável")
        self.pause = pause

"""Política de retentativa dos clientes HTTP da Voyage.

Compartilhada entre o embedder e o reranker porque os dois batem no MESMO provedor,
com a mesma cota e as mesmas regras: duas cópias divergiriam em silêncio, e a divergência
só apareceria em produção.

`429` é o provedor pedindo para esperar; `5xx` é falha dele. Os demais `4xx` (pedido
malformado, chave inválida) não melhoram com repetição.
"""

import asyncio

import httpx

ATTEMPTS = 5

TOO_MANY_REQUESTS = 429

# A janela de cota da Voyage é por MINUTO. Esperar 1s ou 2s não devolve cota nenhuma —
# só queima uma tentativa e conta mais uma requisição contra o limite.
RATE_LIMIT_PAUSE_SECONDS = 20.0
MAX_PAUSE_SECONDS = 120.0


async def default_sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


def is_retryable(status: int) -> bool:
    """5xx é falha do provedor; 429 é o provedor pedindo para esperar. Os demais 4xx
    (pedido malformado, chave inválida) não melhoram com repetição."""
    return status == TOO_MANY_REQUESTS or status >= 500


def pause_for(response: httpx.Response, attempt: int) -> float:
    header = response.headers.get("retry-after", "").strip()
    if header:
        try:
            return min(float(header), MAX_PAUSE_SECONDS)
        except ValueError:
            # `Retry-After` também aceita data HTTP; nesse caso vale o backoff padrão.
            pass
    if response.status_code == TOO_MANY_REQUESTS:
        return min(RATE_LIMIT_PAUSE_SECONDS * (attempt + 1), MAX_PAUSE_SECONDS)
    return float(2**attempt)

"""Embeddings pela Voyage AI, por HTTP.

HTTP direto em vez do SDK do provedor: o `httpx.MockTransport` deixa testar o cliente
inteiro sem rede, e trocar de provedor passa a ser trocar um arquivo.

`input_type` é usado nos dois lados — "document" na ingestão, "query" na busca. Errar
isso degrada a recuperação de um jeito que não aparece em teste funcional.

Sobre limites do provedor: a Voyage restringe **tokens por requisição** e impõe cota por
minuto (RPM e TPM). Um lote pode caber folgado no limite de itens e ainda assim estourar
a cota — por isso o lote é fechado por tokens estimados, e por isso `429` é tratado como
erro **retentável**, não como falha definitiva.
"""

import math
from collections.abc import Awaitable, Callable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx

from sensus_rag.contract import Vector
from sensus_rag.errors import EmbeddingUnavailable
from sensus_rag.http_retry import ATTEMPTS
from sensus_rag.http_retry import default_sleep as _default_sleep
from sensus_rag.http_retry import is_retryable as _is_retryable
from sensus_rag.http_retry import pause_for as _pause_for
from sensus_rag.rate_limit import SlidingWindowLimiter

ENDPOINT = "https://api.voyageai.com/v1/embeddings"

# Teto de itens por requisição, imposto pela API.
MAX_BATCH = 1000

# Teto de tokens por requisição. Conservador de propósito: uma conta sem método de
# pagamento na Voyage fica com 3 RPM e 10K TPM, e um lote acima disso nunca passa,
# por mais que se retente.
MAX_BATCH_TOKENS = 8_000

# Estimativa grosseira de tokenização para PT-BR, sem carregar um tokenizador só para
# dimensionar lote. Subestimar a capacidade é o erro seguro: gera mais requisições.
CHARS_PER_TOKEN = 3


def estimated_tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / CHARS_PER_TOKEN))


@dataclass(frozen=True)
class VoyageEmbedder:
    # `repr=False`: um `repr()` deste objeto (log, traceback) nunca deve imprimir a chave.
    api_key: str = field(repr=False)
    model: str
    dimensions: int
    client: httpx.AsyncClient
    # Injetável para que o teste de retentativa não gaste o tempo que ele mede.
    sleep: Callable[[float], Awaitable[None]] = field(default=_default_sleep)
    # Sem limitador, as requisições saem à vontade — é o certo para uma chave com cota
    # padrão. Numa cota apertada, retentar depois do 429 não basta: a requisição
    # recusada também consome cota, então é preciso espaçar ANTES.
    limiter: SlidingWindowLimiter | None = None
    max_batch_tokens: int = MAX_BATCH_TOKENS

    async def embed_documents(self, texts: Sequence[str]) -> list[Vector]:
        vectors: list[Vector] = []
        for batch in batches(texts, max_tokens=self.max_batch_tokens):
            vectors.extend(await self._request(batch, "document"))
        return vectors

    async def embed_query(self, text: str) -> Vector:
        return (await self._request([text], "query"))[0]

    async def _request(self, texts: list[str], input_type: str) -> list[Vector]:
        if not texts:
            return []

        if self.limiter is not None:
            await self.limiter.acquire(sum(estimated_tokens(t) for t in texts))

        body = {
            "input": texts,
            "model": self.model,
            "input_type": input_type,
            "output_dimension": self.dimensions,
        }
        last_error: Exception | None = None
        for attempt in range(ATTEMPTS):
            try:
                response = await self.client.post(
                    ENDPOINT,
                    json=body,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                response.raise_for_status()
                return self._sort_by_index(response.json())
            except httpx.HTTPStatusError as exc:
                if not _is_retryable(exc.response.status_code):
                    raise EmbeddingUnavailable("provedor de embeddings indisponível") from exc
                last_error = exc
                if attempt < ATTEMPTS - 1:
                    await self.sleep(_pause_for(exc.response, attempt))
            except (httpx.HTTPError, KeyError, ValueError) as exc:
                # Falha de conexão, timeout e resposta ilegível valem uma repetição.
                last_error = exc
                if attempt < ATTEMPTS - 1:
                    await self.sleep(float(2**attempt))

        # A mensagem do provedor pode carregar detalhe interno: fica no `raise from`,
        # que vai para o log, e nunca no texto que a borda HTTP mostra.
        raise EmbeddingUnavailable("provedor de embeddings indisponível") from last_error

    def _sort_by_index(self, payload: dict[str, Any]) -> list[Vector]:
        """A API não promete ordem — o campo `index` é quem manda."""
        data: list[dict[str, Any]] = payload["data"]
        by_index = sorted(data, key=lambda item: int(item["index"]))
        return [tuple(float(v) for v in item["embedding"]) for item in by_index]


def batches(texts: Sequence[str], *, max_tokens: int = MAX_BATCH_TOKENS) -> Iterator[list[str]]:
    """Fecha o lote pelo primeiro teto alcançado: itens ou tokens estimados."""
    batch: list[str] = []
    tokens = 0
    for text in texts:
        estimate = estimated_tokens(text)
        if batch and (len(batch) >= MAX_BATCH or tokens + estimate > max_tokens):
            yield batch
            batch, tokens = [], 0
        batch.append(text)
        tokens += estimate
    if batch:
        yield batch

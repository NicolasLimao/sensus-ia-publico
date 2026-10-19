"""Rerank pela Voyage AI, por HTTP.

HTTP direto em vez do SDK, pelo mesmo motivo do embedder: o `httpx.MockTransport` testa
o cliente inteiro sem rede, e trocar de provedor é trocar um arquivo.

`return_documents` fica em `false` — a resposta traz o ÍNDICE na lista enviada, e o
mapeamento de volta é feito sobre os `RetrievedChunk` que já temos. Sem round-trip de
conteúdo, e sem chance de o texto voltar alterado. Em troca, índice fora da faixa tem de
ser erro explícito: descartá-lo em silêncio corromperia a medição.

`truncation` vai em `true` para que um trecho longo nunca derrube a requisição por
estourar o contexto do modelo.
"""

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

import httpx

from sensus_rag.contract import RetrievedChunk
from sensus_rag.embedding.voyage import estimated_tokens
from sensus_rag.errors import RerankUnavailable
from sensus_rag.http_retry import ATTEMPTS, default_sleep, is_retryable, pause_for
from sensus_rag.rate_limit import SlidingWindowLimiter
from sensus_rag.reranking.budget import documents_for, fit_to_budget

logger = logging.getLogger(__name__)

ENDPOINT = "https://api.voyageai.com/v1/rerank"

# Teto conservador por requisição: a conta sem método de pagamento fica em 10K TPM, e
# uma requisição maior que a janela nunca passa, por mais que o limitador espere.
MAX_REQUEST_TOKENS = 8_000


@dataclass(frozen=True)
class VoyageReranker:
    # `repr=False`: um `repr()` deste objeto (log, traceback) nunca deve imprimir a chave.
    api_key: str = field(repr=False)
    model: str
    client: httpx.AsyncClient
    sleep: Callable[[float], Awaitable[None]] = field(default=default_sleep)
    # Compartilhado com o embedder: é a MESMA conta Voyage, e dois limitadores
    # independentes achariam cada um que têm a cota inteira.
    limiter: SlidingWindowLimiter | None = None
    max_request_tokens: int = MAX_REQUEST_TOKENS
    max_document_chars: int = 0

    async def rerank(
        self, *, question: str, chunks: Sequence[RetrievedChunk], top_k: int
    ) -> list[RetrievedChunk]:
        if not chunks:
            return []

        candidates = list(chunks)
        documents = documents_for(candidates, max_document_chars=self.max_document_chars)
        fitted = fit_to_budget(
            question=question,
            documents=documents,
            max_request_tokens=self.max_request_tokens,
        )
        if fitted < len(documents):
            logger.warning(
                "rerank: %d de %d candidatos couberam no orçamento de %d tokens",
                fitted,
                len(documents),
                self.max_request_tokens,
            )
        candidates, documents = candidates[:fitted], documents[:fitted]

        estimate = estimated_tokens(question) + sum(
            estimated_tokens(document) for document in documents
        )
        if self.limiter is not None:
            await self.limiter.acquire(estimate)

        payload = await self._request(
            {
                "model": self.model,
                "query": question,
                "documents": documents,
                "top_k": min(top_k, len(documents)),
                "truncation": True,
            },
            estimate,
        )
        try:
            return self._ordered(payload, candidates, top_k=top_k)
        except (KeyError, TypeError, ValueError) as exc:
            # Um 200 com JSON válido mas fora do contrato (sem `data`, item sem
            # `relevance_score`) escaparia como erro cru, e o fallback do
            # `RerankingRetriever` só captura `RerankUnavailable` — o chat daria erro
            # em vez de degradar para a ordem fundida.
            raise RerankUnavailable("resposta de rerank ilegível") from exc

    async def _request(self, body: dict[str, Any], estimate: int) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(ATTEMPTS):
            try:
                response = await self.client.post(
                    ENDPOINT,
                    json=body,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                response.raise_for_status()
                payload: dict[str, Any] = response.json()
                self._log_usage(payload, estimate)
                return payload
            except httpx.HTTPStatusError as exc:
                if not is_retryable(exc.response.status_code):
                    raise RerankUnavailable("provedor de rerank indisponível") from exc
                last_error = exc
                if attempt < ATTEMPTS - 1:
                    await self.sleep(pause_for(exc.response, attempt))
            except (httpx.HTTPError, KeyError, ValueError) as exc:
                # Falha de conexão, timeout e resposta ilegível valem uma repetição.
                last_error = exc
                if attempt < ATTEMPTS - 1:
                    await self.sleep(float(2**attempt))

        # A mensagem do provedor pode carregar detalhe interno: fica no `raise from`,
        # que vai para o log, e nunca no texto que a borda HTTP mostra.
        raise RerankUnavailable("provedor de rerank indisponível") from last_error

    def _log_usage(self, payload: dict[str, Any], estimate: int) -> None:
        """O provedor devolve o consumo real. É o único jeito de descobrir que o
        `CHARS_PER_TOKEN = 3` do embedder está longe da verdade para este corpus — e a
        estimativa é o que o limitador usa para espaçar."""
        usage = payload.get("usage") or {}
        real = usage.get("total_tokens")
        if real is not None:
            logger.debug("rerank: %s tokens reais, %d estimados", real, estimate)

    def _ordered(
        self,
        payload: dict[str, Any],
        candidates: Sequence[RetrievedChunk],
        *,
        top_k: int,
    ) -> list[RetrievedChunk]:
        data: list[dict[str, Any]] = payload["data"]
        scored: list[tuple[float, int]] = []
        for item in data:
            index = int(item["index"])
            if not 0 <= index < len(candidates):
                raise RerankUnavailable("provedor de rerank devolveu índice fora da faixa")
            scored.append((float(item["relevance_score"]), index))

        # A doc promete ordem por relevância decrescente; ordenar aqui torna o
        # resultado independente dessa promessa. Empate resolve pela ordem do RRF.
        scored.sort(key=lambda pair: (-pair[0], pair[1]))

        ordered: list[RetrievedChunk] = []
        seen: set[int] = set()
        for score, index in scored:
            if index in seen:
                # Repetir um índice deslocaria as posições seguintes e contaria o mesmo
                # acerto duas vezes. Fica o melhor score — a lista já está ordenada.
                logger.warning("rerank: provedor repetiu o índice %d; ignorado", index)
                continue
            seen.add(index)
            ordered.append(replace(candidates[index], rerank_score=score, fused_rank=index + 1))

        # `top_k` vai no corpo da requisição, mas quem garante o tamanho é este corte:
        # confiar na promessa do provedor deixaria a lista reordenada mais longa que a
        # fundida, e um acerto fora da janela de leitura contaria como se estivesse
        # dentro dela.
        return ordered[:top_k]

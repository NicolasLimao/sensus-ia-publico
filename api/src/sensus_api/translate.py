"""Tradução: eventos do upstream viram o contrato SSE do Sensus.

Esta é a peça central do adaptador: a sequência de eventos, o mapeamento de erro e o
que entra no histórico.
"""

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator

from sensus_api.errors import code_for
from sensus_api.sse import format_event
from sensus_api.store import ConversationStore, StoredMessage
from sensus_api.upstream.contract import (
    Chunk,
    ResultEvent,
    StageEvent,
    TokenEvent,
    UpstreamClient,
)

logger = logging.getLogger(__name__)


def _chunk_payload(chunk: Chunk) -> dict[str, object]:
    return {
        "id": chunk.id,
        "excerpt": chunk.excerpt,
        "document": chunk.document,
        "score": chunk.score,
    }


async def stream_chat(
    client: UpstreamClient,
    *,
    question: str,
    user_id: str,
    tenant_id: str,
    store: ConversationStore,
    timeout_seconds: float = 90.0,
) -> AsyncIterator[str]:
    """Consome o upstream e emite blocos SSE já formatados."""
    started = time.monotonic()
    await store.append(user_id, StoredMessage(id=uuid.uuid4().hex, role="user", content=question))

    answer: list[str] = []
    chunks: tuple[Chunk, ...] = ()
    score: float | None = None

    try:
        async with asyncio.timeout(timeout_seconds):
            async for event in client.ask(question=question, user_id=user_id, tenant_id=tenant_id):
                match event:
                    case StageEvent(stage=stage):
                        yield format_event("stage", {"stage": stage})
                    case TokenEvent(text=text):
                        answer.append(text)
                        yield format_event("token", {"text": text})
                    case ResultEvent(chunks=result_chunks, score=result_score):
                        chunks, score = result_chunks, result_score
    # Captura ampla de propósito: qualquer falha do upstream vira erro do Sensus, e
    # nenhuma exceção dele deve escapar para o navegador.
    except Exception as exc:
        code, message = code_for(exc)
        # Log sem o texto da mensagem: é PII do usuário final.
        logger.warning(
            "falha no upstream: code=%s question_len=%d elapsed_ms=%d",
            code,
            len(question),
            int((time.monotonic() - started) * 1000),
            exc_info=exc,
        )
        yield format_event("error", {"code": code, "message": message})
        return

    yield format_event("sources", {"sources": [_chunk_payload(c) for c in chunks]})

    message_id = uuid.uuid4().hex
    await store.append(
        user_id,
        StoredMessage(
            id=message_id,
            role="assistant",
            content="".join(answer),
            sources=chunks,
            chunks_used=len(chunks),
        ),
    )
    elapsed_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "resposta concluida: chunks_used=%d elapsed_ms=%d answer_len=%d",
        len(chunks),
        elapsed_ms,
        len("".join(answer)),
    )
    yield format_event(
        "done",
        {
            "message_id": message_id,
            "chunks_used": len(chunks),
            "score": score,
            "elapsed_ms": elapsed_ms,
        },
    )

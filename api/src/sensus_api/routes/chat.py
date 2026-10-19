"""Rota de chat: recebe a pergunta e devolve o stream SSE do Sensus."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from sensus_api.principal import Principal, require_session
from sensus_api.translate import stream_chat

router = APIRouter(prefix="/api/v1")


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)

    @field_validator("message")
    @classmethod
    def nao_pode_ser_so_espaco(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("mensagem vazia")
        return stripped


@router.post("/chat")
async def chat(
    payload: ChatRequest,
    request: Request,
    principal: Annotated[Principal, Depends(require_session)],
) -> StreamingResponse:
    settings = request.app.state.settings
    stream = stream_chat(
        request.app.state.upstream,
        question=payload.message,
        user_id=principal.user_id,
        tenant_id=principal.tenant_id,
        store=request.app.state.store,
        timeout_seconds=settings.upstream_timeout_seconds,
    )
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Desliga o buffer de proxies, que engoliria o streaming.
            "X-Accel-Buffering": "no",
        },
    )

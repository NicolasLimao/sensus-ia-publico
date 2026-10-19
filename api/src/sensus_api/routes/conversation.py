"""Restauração da conversa da sessão."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from sensus_api.principal import Principal, require_session
from sensus_api.store import Rating, Role, StoredMessage

router = APIRouter(prefix="/api/v1")


class SourceResponse(BaseModel):
    id: str
    excerpt: str
    document: str | None
    score: float


class MessageResponse(BaseModel):
    id: str
    role: Role
    content: str
    sources: list[SourceResponse]
    chunks_used: int
    feedback: Rating | None


class ConversationResponse(BaseModel):
    messages: list[MessageResponse]


def _to_response(message: StoredMessage) -> MessageResponse:
    return MessageResponse(
        id=message.id,
        role=message.role,
        content=message.content,
        sources=[
            SourceResponse(id=c.id, excerpt=c.excerpt, document=c.document, score=c.score)
            for c in message.sources
        ],
        chunks_used=message.chunks_used,
        feedback=message.feedback,
    )


@router.get("/conversation", response_model=ConversationResponse)
async def get_conversation(
    request: Request, principal: Annotated[Principal, Depends(require_session)]
) -> ConversationResponse:
    history = await request.app.state.store.history(principal.user_id)
    return ConversationResponse(messages=[_to_response(m) for m in history])

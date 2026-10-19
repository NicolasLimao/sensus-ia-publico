"""Feedback 👍/👎 por mensagem."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel

from sensus_api.principal import Principal, require_session

router = APIRouter(prefix="/api/v1")


class FeedbackRequest(BaseModel):
    rating: Literal["up", "down"]


@router.post("/messages/{message_id}/feedback", status_code=status.HTTP_204_NO_CONTENT)
async def set_feedback(
    message_id: str,
    payload: FeedbackRequest,
    request: Request,
    principal: Annotated[Principal, Depends(require_session)],
) -> Response:
    # `set_feedback` só encontra mensagens da própria sessão — feedback em mensagem de
    # outra pessoa é indistinguível de mensagem inexistente, e é assim que deve ser.
    updated = await request.app.state.store.set_feedback(
        principal.user_id, message_id, payload.rating
    )
    if not updated:
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "Mensagem não encontrada."},
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)

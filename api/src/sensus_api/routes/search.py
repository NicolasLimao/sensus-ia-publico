"""Busca sobre a base do tenant. É por aqui que o harness de medição e a depuração passam."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from sensus_api.principal import Principal, require_session
from sensus_api.rag_errors import rag_error_for
from sensus_rag.errors import IngestionError

router = APIRouter(prefix="/api/v1")


class SearchRequest(BaseModel):
    question: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=50)

    @field_validator("question")
    @classmethod
    def cannot_be_only_whitespace(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("pergunta vazia")
        return value


class SearchResult(BaseModel):
    """`semantic_rank`/`lexical_rank` são diagnóstico: a posição do trecho em cada
    perna da busca, `null` quando aquela perna não o devolveu. É por aqui que o harness
    de medição enxerga se as duas pernas estão vivas — um resultado inteiro com
    `lexical_rank` nulo significa índice de texto quebrado, não relevância baixa.

    `rerank_score` e `fused_rank` contam a mesma história para o rerank: a relevância
    do cross-encoder e a posição que o trecho ocupava na lista fundida. Um resultado
    inteiro com `rerank_score` nulo, havendo rerank configurado, significa provedor de
    rerank fora do ar — o decorador caiu para a ordem do RRF —, não relevância baixa."""

    chunk_id: str
    document_id: str
    document_name: str
    excerpt: str
    score: float
    semantic_rank: int | None
    lexical_rank: int | None
    rerank_score: float | None
    fused_rank: int | None


class SearchResponse(BaseModel):
    results: list[SearchResult]


@router.post("/search")
async def search(
    payload: SearchRequest,
    request: Request,
    principal: Annotated[Principal, Depends(require_session)],
) -> SearchResponse:
    try:
        found = await request.app.state.retriever.search(
            tenant_id=principal.tenant_id,
            question=payload.question,
            top_k=payload.top_k,
        )
    except IngestionError as exc:
        http_status, code, message = rag_error_for(exc)
        raise HTTPException(
            status_code=http_status, detail={"code": code, "message": message}
        ) from exc
    return SearchResponse(
        results=[
            SearchResult(
                chunk_id=c.chunk_id,
                document_id=c.document_id,
                document_name=c.document_name,
                excerpt=c.content,
                score=c.score,
                semantic_rank=c.semantic_rank,
                lexical_rank=c.lexical_rank,
                rerank_score=c.rerank_score,
                fused_rank=c.fused_rank,
            )
            for c in found
        ]
    )

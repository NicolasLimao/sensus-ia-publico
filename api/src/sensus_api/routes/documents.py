"""Ingestão de documentos por tenant.

Toda rota daqui exige sessão, e as que ESCREVEM exigem também papel de admin: documento
é do tenant, e quem muda o acervo muda o que a organização inteira lê. A checagem de
papel mora aqui, na borda — a policy do banco olha só o tenant, porque o `sensus_rag`
é o RAG genérico e não conhece usuário (spec §5.3).
"""

from typing import Annotated, Protocol

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel

from sensus_api.principal import Principal, require_admin, require_session
from sensus_api.rag_errors import rag_error_for
from sensus_rag.errors import DocumentTooLarge, IngestionError

router = APIRouter(prefix="/api/v1")

READ_CHUNK = 64 * 1024


class UploadSource(Protocol):
    """O que `read_within` precisa de um upload: ler em pedaços. `UploadFile` satisfaz."""

    async def read(self, size: int = -1) -> bytes: ...


async def read_within(source: UploadSource, *, name: str, max_bytes: int) -> bytes:
    """Lê o upload em pedaços e aborta no instante em que passa do teto.

    Um `await file.read()` inteiro antes de conferir o tamanho faz o teto não proteger
    nada: quando o 413 é decidido, o arquivo já está inteiro na memória do processo, e
    é exatamente contra isso que o limite existe. O `DocumentIngestor` confere de novo
    — ele também roda fora da borda HTTP, pelo harness de medição — mas a defesa que
    importa para quem serve HTTP é esta.
    """
    buffer = bytearray()
    while piece := await source.read(READ_CHUNK):
        buffer += piece
        if len(buffer) > max_bytes:
            raise DocumentTooLarge(f"'{name}' passa do limite de {max_bytes} bytes.")
    return bytes(buffer)


class DocumentResponse(BaseModel):
    document_id: str
    name: str
    chunk_count: int


class DocumentSummaryResponse(BaseModel):
    id: str
    name: str
    chunk_count: int
    created_at: str


class DocumentListResponse(BaseModel):
    documents: list[DocumentSummaryResponse]


@router.post("/documents", status_code=status.HTTP_201_CREATED)
async def upload_document(
    request: Request,
    principal: Annotated[Principal, Depends(require_session)],
    file: UploadFile = File(...),
) -> DocumentResponse:
    require_admin(principal)
    ingestor = request.app.state.ingestor
    name = file.filename or "sem-nome"
    try:
        data = await read_within(file, name=name, max_bytes=ingestor.max_bytes)
        result = await ingestor.ingest(
            tenant_id=principal.tenant_id,
            name=name,
            content_type=file.content_type or "application/octet-stream",
            data=data,
        )
    except IngestionError as exc:
        http_status, code, message = rag_error_for(exc)
        raise HTTPException(
            status_code=http_status, detail={"code": code, "message": message}
        ) from exc
    return DocumentResponse(
        document_id=result.document_id,
        name=result.name,
        chunk_count=result.chunk_count,
    )


@router.get("/documents")
async def list_documents(
    request: Request, principal: Annotated[Principal, Depends(require_session)]
) -> DocumentListResponse:
    try:
        documents = await request.app.state.repository.list_documents(tenant_id=principal.tenant_id)
    except IngestionError as exc:
        http_status, code, message = rag_error_for(exc)
        raise HTTPException(
            status_code=http_status, detail={"code": code, "message": message}
        ) from exc
    return DocumentListResponse(
        documents=[
            DocumentSummaryResponse(
                id=d.id,
                name=d.name,
                chunk_count=d.chunk_count,
                created_at=d.created_at.isoformat(),
            )
            for d in documents
        ]
    )


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(require_session)],
) -> Response:
    require_admin(principal)
    try:
        deleted = await request.app.state.repository.delete_document(
            tenant_id=principal.tenant_id, document_id=document_id
        )
    except IngestionError as exc:
        http_status, code, message = rag_error_for(exc)
        raise HTTPException(
            status_code=http_status, detail={"code": code, "message": message}
        ) from exc
    if not deleted:
        # Documento de outro tenant é indistinguível de inexistente — e é assim que deve ser.
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "Documento não encontrado."},
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)

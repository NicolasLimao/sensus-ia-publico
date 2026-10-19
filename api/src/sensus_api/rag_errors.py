"""Tradução das falhas do pipeline para a borda HTTP.

Mesmo princípio de `errors.py`: a exceção pode carregar nome de tabela ou detalhe do
provedor; para o navegador vai só o código e uma frase nossa.
"""

from typing import Literal

from sensus_rag.errors import (
    DocumentTooLarge,
    EmbeddingUnavailable,
    EmptyDocument,
    RepositoryUnavailable,
    UnsupportedDocument,
)

RagErrorCode = Literal[
    "unsupported_document",
    "empty_document",
    "document_too_large",
    "service_unavailable",
]

_STATUS: dict[RagErrorCode, int] = {
    "unsupported_document": 415,
    "empty_document": 400,
    "document_too_large": 413,
    "service_unavailable": 503,
}

_MESSAGES: dict[RagErrorCode, str] = {
    "unsupported_document": "Formato não suportado. Aceitos: PDF com texto, .txt e .md.",
    "empty_document": (
        "Não foi possível extrair texto desse arquivo. "
        "PDF escaneado exige OCR, que ainda não é suportado."
    ),
    "document_too_large": "Arquivo grande demais.",
    "service_unavailable": "O serviço está indisponível agora. Tente de novo em instantes.",
}


def rag_error_for(exc: BaseException) -> tuple[int, RagErrorCode, str]:
    """Mapeia a exceção para (status HTTP, código, mensagem segura de exibir)."""
    if isinstance(exc, UnsupportedDocument):
        code: RagErrorCode = "unsupported_document"
    elif isinstance(exc, EmptyDocument):
        code = "empty_document"
    elif isinstance(exc, DocumentTooLarge):
        code = "document_too_large"
    elif isinstance(exc, EmbeddingUnavailable | RepositoryUnavailable):
        code = "service_unavailable"
    else:
        code = "service_unavailable"
    return _STATUS[code], code, _MESSAGES[code]

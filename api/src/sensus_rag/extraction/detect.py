"""Escolha do extractor. O content-type manda; a extensão é o desempate."""

from collections.abc import Callable
from pathlib import Path

from sensus_rag.contract import DocumentExtractor
from sensus_rag.errors import UnsupportedDocument
from sensus_rag.extraction.pdf import PdfExtractor
from sensus_rag.extraction.plain import PlainTextExtractor

# `Callable[[], DocumentExtractor]` e não `type[DocumentExtractor]`: o mypy recusa
# instanciar um Protocol por sua classe.
_BY_CONTENT_TYPE: dict[str, Callable[[], DocumentExtractor]] = {
    "application/pdf": PdfExtractor,
    "text/plain": PlainTextExtractor,
    "text/markdown": PlainTextExtractor,
}

_BY_EXTENSION: dict[str, Callable[[], DocumentExtractor]] = {
    ".pdf": PdfExtractor,
    ".txt": PlainTextExtractor,
    ".md": PlainTextExtractor,
}

SUPPORTED = "PDF com texto, .txt e .md"


def extractor_for(content_type: str, name: str) -> DocumentExtractor:
    mime_type = content_type.split(";")[0].strip().lower()
    chosen = _BY_CONTENT_TYPE.get(mime_type) or _BY_EXTENSION.get(Path(name).suffix.lower())
    if chosen is None:
        raise UnsupportedDocument(f"Formato não suportado. Aceitos: {SUPPORTED}.")
    return chosen()

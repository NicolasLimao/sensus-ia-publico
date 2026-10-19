"""PDF com camada de texto. Sem OCR — PDF escaneado é recusado com erro explícito."""

import io
from dataclasses import dataclass

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from sensus_rag.errors import EmptyDocument, UnsupportedDocument
from sensus_rag.extraction.normalize import normalize_pdf


@dataclass(frozen=True)
class PdfExtractor:
    def extract(self, data: bytes, name: str) -> str:
        try:
            reader = PdfReader(io.BytesIO(data))
            pages = [page.extract_text() or "" for page in reader.pages]
        except (PdfReadError, ValueError, OSError) as exc:
            raise UnsupportedDocument(f"PDF ilegível: {name}") from exc

        text = normalize_pdf("\n\n".join(pages))
        if not text:
            raise EmptyDocument(
                f"'{name}' não tem camada de texto. PDF escaneado exige OCR, "
                "que ainda não é suportado."
            )
        return text

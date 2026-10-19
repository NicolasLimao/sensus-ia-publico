"""Texto puro e Markdown."""

from dataclasses import dataclass

from sensus_rag.extraction.normalize import normalize_plain


@dataclass(frozen=True)
class PlainTextExtractor:
    def extract(self, data: bytes, name: str) -> str:
        # `errors="replace"` porque um byte inválido não justifica recusar o documento.
        return normalize_plain(data.decode("utf-8", errors="replace"))

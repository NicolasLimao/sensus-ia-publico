"""Chunking estrutural: respeita parágrafos, agrupa até um alvo, sobrepõe as bordas.

Os parâmetros são argumentos do construtor de propósito — o harness de medição varre
configurações sem editar código.
"""

import re
from dataclasses import dataclass
from itertools import pairwise

from sensus_rag.contract import TextChunk

_PARAGRAPH = re.compile(r"\n\s*\n")


@dataclass(frozen=True)
class StructuralChunker:
    target_chars: int = 1200
    overlap_chars: int = 150

    def split(self, text: str) -> list[TextChunk]:
        blocks = self._blocks(text)
        if not blocks:
            return []

        chunks: list[TextChunk] = []
        current: list[tuple[int, str]] = []
        size = 0

        for start, block in blocks:
            for piece_start, piece in self._limit(start, block):
                if current and size + len(piece) > self.target_chars:
                    chunks.append(self._build(text, current, len(chunks)))
                    current, size = [], 0
                current.append((piece_start, piece))
                size += len(piece)

        if current:
            chunks.append(self._build(text, current, len(chunks)))
        return self._add_overlap(chunks)

    def _blocks(self, text: str) -> list[tuple[int, str]]:
        """Parágrafos não vazios, com o offset onde cada um começa no texto original."""
        blocks: list[tuple[int, str]] = []
        position = 0
        for raw in _PARAGRAPH.split(text):
            start = text.index(raw, position) if raw else position
            position = start + len(raw)
            if raw.strip():
                indent = len(raw) - len(raw.lstrip())
                blocks.append((start + indent, raw.strip()))
        return blocks

    def _limit(self, start: int, block: str) -> list[tuple[int, str]]:
        """Um parágrafo maior que o alvo é cortado em pedaços do tamanho do alvo."""
        if len(block) <= self.target_chars:
            return [(start, block)]
        return [
            (start + i, block[i : i + self.target_chars])
            for i in range(0, len(block), self.target_chars)
        ]

    def _build(self, text: str, parts: list[tuple[int, str]], index: int) -> TextChunk:
        start = parts[0][0]
        end = parts[-1][0] + len(parts[-1][1])
        return TextChunk(content=text[start:end], index=index, start=start, end=end)

    def _add_overlap(self, chunks: list[TextChunk]) -> list[TextChunk]:
        """Prefixa cada chunk com o fim do anterior. `start`/`end` delimitam apenas
        o trecho próprio do chunk; o prefixo emprestado fica fora de [start, end)."""
        if self.overlap_chars <= 0 or len(chunks) < 2:
            return chunks
        result = [chunks[0]]
        for previous, current in pairwise(chunks):
            tail = previous.content[-self.overlap_chars :]
            result.append(
                TextChunk(
                    content=tail + current.content,
                    index=current.index,
                    start=current.start,
                    end=current.end,
                )
            )
        return result

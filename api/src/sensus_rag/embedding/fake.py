"""Embedder determinístico para teste.

Hash do texto semeia um vetor estável e normalizado. Textos iguais dão vetores iguais,
textos diferentes dão vetores diferentes — o suficiente para exercitar o pipeline
inteiro sem gastar uma chamada de API.
"""

import hashlib
import math
import random
from collections.abc import Sequence
from dataclasses import dataclass

from sensus_rag.contract import Vector


@dataclass(frozen=True)
class FakeEmbedder:
    dimensions: int = 1024

    async def embed_documents(self, texts: Sequence[str]) -> list[Vector]:
        return [self._vector(text) for text in texts]

    async def embed_query(self, text: str) -> Vector:
        return self._vector(text)

    def _vector(self, text: str) -> Vector:
        seed = int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")
        generator = random.Random(seed)
        raw = [generator.uniform(-1.0, 1.0) for _ in range(self.dimensions)]
        norm = math.sqrt(sum(v * v for v in raw)) or 1.0
        return tuple(v / norm for v in raw)

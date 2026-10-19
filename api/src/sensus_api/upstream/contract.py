"""Contrato único com o upstream.

Tudo que o adaptador sabe sobre o upstream passa por aqui — é isso que permite
desenvolver contra um duplo hoje e trocar por HTTP real depois sem tocar em mais nada.
Vocabulário deliberadamente genérico: nenhum termo de domínio de produto anterior.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal, Protocol

Stage = Literal["retrieving", "reranking", "generating"]


@dataclass(frozen=True)
class Chunk:
    """Trecho recuperado. `document` nasce `None` — a fatia 2 o preenche."""

    id: str
    excerpt: str
    score: float
    document: str | None = None


@dataclass(frozen=True)
class StageEvent:
    stage: Stage


@dataclass(frozen=True)
class TokenEvent:
    text: str


@dataclass(frozen=True)
class ResultEvent:
    """Último evento: os trechos que fundamentaram a resposta."""

    chunks: tuple[Chunk, ...]
    score: float | None


UpstreamEvent = StageEvent | TokenEvent | ResultEvent


class UpstreamError(Exception):
    """Base das falhas do upstream. Nunca atravessa para o navegador."""


class UpstreamTimeout(UpstreamError):
    pass


class UpstreamUnavailable(UpstreamError):
    pass


class UpstreamBadRequest(UpstreamError):
    pass


class UpstreamClient(Protocol):
    def ask(
        self, *, question: str, user_id: str, tenant_id: str
    ) -> AsyncIterator[UpstreamEvent]: ...

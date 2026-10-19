"""Contrato do pipeline de RAG.

Tudo que atravessa as etapas passa por aqui. É o que permite trocar Voyage por outro
provedor, ou Supabase por Postgres local, sem tocar em quem consome — a mesma costura
que `sensus_api/upstream/contract.py` usa para o upstream.

Nenhuma implementação neste arquivo. `sensus_rag` nunca importa `sensus_api`.
"""

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

Vector = tuple[float, ...]


@dataclass(frozen=True)
class TextChunk:
    """Trecho de um documento. `start`/`end` delimitam o trecho próprio do chunk.
    Com sobreposição, `content` pode trazer um prefixo do chunk anterior antes desse intervalo."""

    content: str
    index: int
    start: int
    end: int


@dataclass(frozen=True)
class EmbeddedChunk:
    chunk: TextChunk
    embedding: Vector


@dataclass(frozen=True)
class ScoredChunk:
    """Resultado de uma das pernas da busca. `score` só é comparável dentro da mesma perna."""

    chunk_id: str
    document_id: str
    document_name: str
    content: str
    score: float


@dataclass(frozen=True)
class RetrievedChunk:
    """Resultado final, já fundido. `score` é o do RRF.

    `semantic_rank` e `lexical_rank` são a posição (1-based) que o chunk ocupou em
    cada perna da busca, ou `None` quando aquela perna não o devolveu. São diagnóstico,
    como a SPEC §3.2 pede: sem eles, uma perna quebrada — índice lexical vazio, vetor
    de dimensão errada — some dentro do score fundido sem deixar sinal, e a medição
    registra uma linha de base que parece válida e não é.

    `rerank_score` é a relevância devolvida pelo cross-encoder e `fused_rank` é a
    posição que o chunk ocupava na lista fundida. `score` continua sendo o do RRF —
    sobrescrevê-lo apagaria o único número comparável entre as duas ordens.

    `rerank_score is None` significa "este resultado não passou por rerank". É assim
    que o fallback do decorador deixa de ser silencioso: um resultado inteiro com
    `rerank_score` nulo, havendo rerank configurado, quer dizer provedor fora do ar,
    não relevância baixa.
    """

    chunk_id: str
    document_id: str
    document_name: str
    content: str
    score: float
    semantic_rank: int | None = None
    lexical_rank: int | None = None
    rerank_score: float | None = None
    fused_rank: int | None = None


@dataclass(frozen=True)
class DocumentSummary:
    id: str
    name: str
    chunk_count: int
    created_at: datetime


@dataclass(frozen=True)
class IngestionResult:
    document_id: str
    name: str
    chunk_count: int


class DocumentExtractor(Protocol):
    def extract(self, data: bytes, name: str) -> str: ...


class Chunker(Protocol):
    def split(self, text: str) -> list[TextChunk]: ...


class Embedder(Protocol):
    async def embed_documents(self, texts: Sequence[str]) -> list[Vector]: ...

    async def embed_query(self, text: str) -> Vector: ...


class ChunkRepository(Protocol):
    """Toda função recebe `tenant_id`. Não existe sobrecarga sem ele — esquecer o
    filtro tem de ser erro de tipo, não bug silencioso.

    `delete_document` devolve `False` para qualquer `document_id` que não seja um
    documento deste tenant — inexistente, de outro tenant, ou malformado. Nenhum desses
    é falha de infraestrutura, então nenhum levanta `RepositoryUnavailable`: o duplo em
    memória e o Postgres têm de responder igual, e `tests/rag/test_repository_contract.py`
    cobra isso dos dois.
    """

    async def upsert_document(
        self,
        *,
        tenant_id: str,
        name: str,
        content_type: str,
        content_hash: str,
        raw_text: str,
        chunks: Sequence[EmbeddedChunk],
    ) -> str: ...

    async def list_documents(self, *, tenant_id: str) -> list[DocumentSummary]: ...

    async def delete_document(self, *, tenant_id: str, document_id: str) -> bool: ...

    async def search_semantic(
        self, *, tenant_id: str, embedding: Vector, limit: int
    ) -> list[ScoredChunk]: ...

    async def search_lexical(
        self, *, tenant_id: str, query: str, limit: int
    ) -> list[ScoredChunk]: ...


class Retriever(Protocol):
    async def search(
        self, *, tenant_id: str, question: str, top_k: int
    ) -> list[RetrievedChunk]: ...


class Reranker(Protocol):
    """Reordena a lista fundida lendo pergunta e trecho juntos.

    Reordena e corta — nunca inventa item nem perde campo. Recebe a lista já na ordem
    do RRF, então é a única peça que conhece as duas ordens ao mesmo tempo: é ela quem
    preenche `rerank_score` e `fused_rank`.
    """

    async def rerank(
        self, *, question: str, chunks: Sequence[RetrievedChunk], top_k: int
    ) -> list[RetrievedChunk]: ...


@dataclass(frozen=True)
class AnswerToken:
    """Um pedaço do texto da resposta, como veio do provedor."""

    text: str


@dataclass(frozen=True)
class AnswerRefusal:
    """Os trechos não sustentam resposta. Nenhum token de resposta foi emitido.

    É um evento, não uma frase: `sensus_rag` não carrega texto de interface. Quem
    escreve o que o usuário lê é o adaptador, junto do resto da copy do produto.
    """


GenerationEvent = AnswerToken | AnswerRefusal


DEFAULT_MAX_CONTEXT_CHARS = 12_000


@dataclass(frozen=True)
class GenerationConfig:
    """O que muda de uma requisição para outra na geração.

    Sem tenant, sem versão, sem id: `sensus_rag` é o RAG genérico e não sabe que
    tenants existem. Quem traduz a configuração de um tenant nisto é o adaptador
    (`sensus_api`), e essa fronteira é a mesma que `users.py` documenta.

    É um objeto, e não três parâmetros soltos, porque ele vai crescer: cada campo novo
    mudaria a assinatura de todos os implementadores de novo.
    """

    model: str
    briefing: str = ""
    max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS


class AnswerGenerator(Protocol):
    """Gera a resposta a partir dos trechos recuperados — e só deles."""

    def generate(
        self,
        *,
        question: str,
        chunks: Sequence[RetrievedChunk],
        config: GenerationConfig,
    ) -> AsyncIterator[GenerationEvent]: ...

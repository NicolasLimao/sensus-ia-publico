"""Falhas do pipeline. A borda HTTP traduz cada uma para um código; nada disso
chega ao navegador com detalhe interno."""


class IngestionError(Exception):
    """Base das falhas de ingestão e recuperação."""


class UnsupportedDocument(IngestionError):
    """Formato que não sabemos ler."""


class EmptyDocument(IngestionError):
    """Nenhum texto extraído — o caso do PDF escaneado, sem camada de texto."""


class DocumentTooLarge(IngestionError):
    """Acima do teto configurado."""


class EmbeddingUnavailable(IngestionError):
    """Provedor de embeddings falhou ou não respondeu."""


class RepositoryUnavailable(IngestionError):
    """Banco falhou ou não respondeu."""


class RerankUnavailable(IngestionError):
    """Provedor de rerank falhou, recusou ou respondeu de forma ilegível."""


class GenerationError(Exception):
    """Base das falhas de geração.

    Irmã de `IngestionError`, não filha: geração não é ingestão, e `rag_error_for`
    (que serve as rotas de `/documents`) não pode traduzir falha de geração para
    `415` ou `413`.
    """


class GenerationUnavailable(GenerationError):
    """Provedor de geração falhou, recusou ou respondeu de forma ilegível."""


class GenerationTimeout(GenerationError):
    """Provedor de geração não respondeu no prazo."""

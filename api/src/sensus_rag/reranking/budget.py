"""Quanto cabe numa requisição de rerank.

A restrição que aperta não é da API — a Voyage aceita 600K tokens e 1000 documentos por
requisição. É da CONTA: sem método de pagamento são 10K tokens por minuto, e uma
requisição que sozinha passa disso nunca cabe na janela, por mais que o limitador espere
(ver o comentário de `_wait_for_tokens` em `rate_limit.py`).

Por isso a lista é cortada ANTES de sair: degradar de 20 candidatos para 17,
previsivelmente e com log, é melhor que estourar a janela.
"""

from collections.abc import Sequence

from sensus_rag.contract import RetrievedChunk
from sensus_rag.embedding.voyage import estimated_tokens


def documents_for(chunks: Sequence[RetrievedChunk], *, max_document_chars: int) -> list[str]:
    """O texto que vai ao reranker. Truncar aqui não altera o chunk devolvido."""
    if max_document_chars <= 0:
        return [chunk.content for chunk in chunks]
    return [chunk.content[:max_document_chars] for chunk in chunks]


def fit_to_budget(*, question: str, documents: Sequence[str], max_request_tokens: int) -> int:
    """Quantos documentos cabem no orçamento, contando a pergunta.

    Corta pela cauda: os piores colocados do RRF saem primeiro. Nunca devolve zero —
    se o primeiro documento sozinho já estoura, ele vai assim mesmo e o provedor
    trunca, o que é melhor que transformar a pergunta em falha de recuperação.
    """
    if max_request_tokens <= 0:
        return len(documents)

    budget = max_request_tokens - estimated_tokens(question)
    used = 0
    fitted = 0
    for document in documents:
        used += estimated_tokens(document)
        if used > budget and fitted > 0:
            break
        fitted += 1
    return fitted

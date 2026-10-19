"""Guarda de orçamento: funções puras, sem rede.

A restrição não é da API da Voyage (600K tokens por requisição) — é da CONTA, que sem
método de pagamento dá 10K tokens por minuto. Uma requisição que sozinha passa disso
nunca cabe na janela, por mais que o limitador espere.
"""

from sensus_rag.contract import RetrievedChunk
from sensus_rag.embedding.voyage import CHARS_PER_TOKEN
from sensus_rag.reranking.budget import documents_for, fit_to_budget


def chunk(content: str) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id="c1",
        document_id="d1",
        document_name="lei.md",
        content=content,
        score=0.5,
    )


def test_sem_teto_de_chars_o_texto_vai_inteiro() -> None:
    chunks = [chunk("a" * 5000)]

    assert documents_for(chunks, max_document_chars=0) == ["a" * 5000]


def test_teto_de_chars_trunca_o_que_o_reranker_le() -> None:
    chunks = [chunk("a" * 5000)]

    enviados = documents_for(chunks, max_document_chars=300)

    assert enviados == ["a" * 300]


def test_truncagem_nao_altera_o_chunk() -> None:
    """O `RetrievedChunk` devolvido ao pipeline continua íntegro: a truncagem afeta o
    que o reranker LÊ, nunca o que a medição mede nem o que chega ao prompt."""
    original = chunk("a" * 5000)

    documents_for([original], max_document_chars=300)

    assert len(original.content) == 5000


def test_tudo_cabendo_devolve_todos() -> None:
    documents = ["a" * (10 * CHARS_PER_TOKEN)] * 5

    assert fit_to_budget(question="p", documents=documents, max_request_tokens=8_000) == 5


def test_corta_a_cauda_quando_estoura() -> None:
    """Sai o pior colocado do RRF primeiro — mesmo critério do corte de contexto da
    geração. Degradar previsivelmente é melhor que estourar a janela de cota."""
    documents = ["a" * (1_000 * CHARS_PER_TOKEN)] * 20

    cabem = fit_to_budget(question="p", documents=documents, max_request_tokens=8_000)

    assert cabem == 7


def test_a_pergunta_conta_no_orcamento() -> None:
    documents = ["a" * (1_000 * CHARS_PER_TOKEN)] * 5
    pergunta = "b" * (5_000 * CHARS_PER_TOKEN)

    cabem = fit_to_budget(question=pergunta, documents=documents, max_request_tokens=8_000)

    assert cabem == 3


def test_um_documento_gigante_sozinho_ainda_e_enviado() -> None:
    """Nem esvaziando tudo cabe. Enviar um e deixar o provedor truncar é melhor que
    devolver lista vazia e transformar a pergunta em falha de recuperação."""
    documents = ["a" * (50_000 * CHARS_PER_TOKEN)]

    assert fit_to_budget(question="p", documents=documents, max_request_tokens=8_000) == 1


def test_teto_zerado_desliga_a_guarda() -> None:
    documents = ["a" * (50_000 * CHARS_PER_TOKEN)] * 3

    assert fit_to_budget(question="p", documents=documents, max_request_tokens=0) == 3

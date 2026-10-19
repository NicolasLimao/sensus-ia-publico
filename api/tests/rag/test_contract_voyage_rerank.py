"""Contrato com a Voyage real. NÃO roda na suíte padrão (`-m contract`).

O duplo pode deixar de corresponder ao provedor em silêncio; este teste é o que faz a
divergência aparecer. Gasta uma chamada da cota — por isso é uma só, e pequena.

A chave e o modelo vêm das settings (fixtures de `tests/conftest.py`), não de `os.environ`:
lendo do ambiente este teste PULAVA numa máquina configurada por `.env`, que é o pior
resultado possível para um teste que existe para pegar divergência em silêncio.
"""

import httpx
import pytest

from sensus_rag.contract import RetrievedChunk
from sensus_rag.reranking.voyage import VoyageReranker

pytestmark = pytest.mark.contract


def _chunk(chunk_id: str, content: str) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        document_id="d1",
        document_name="lei.md",
        content=content,
        score=0.5,
    )


async def test_a_voyage_reordena_como_o_contrato_promete(
    voyage_api_key: str, rerank_model: str
) -> None:
    async with httpx.AsyncClient(timeout=60.0) as client:
        reranker = VoyageReranker(
            api_key=voyage_api_key,
            model=rerank_model,
            client=client,
        )
        ordenados = await reranker.rerank(
            question="qual o prazo de entrega do produto?",
            chunks=[
                _chunk("c1", "O horário de funcionamento das lojas é das 9h às 18h."),
                _chunk("c2", "O prazo de entrega do produto é de até 30 dias úteis."),
            ],
            top_k=2,
        )

    assert [c.chunk_id for c in ordenados] == ["c2", "c1"]
    assert ordenados[0].rerank_score is not None
    assert ordenados[0].fused_rank == 2
    assert ordenados[0].content.startswith("O prazo de entrega")

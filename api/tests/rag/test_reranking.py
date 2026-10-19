"""Rerank: contrato, duplo e decorador.

Nenhum teste aqui toca rede — o cliente HTTP tem arquivo próprio
(`test_reranking_voyage.py`).
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field, replace

import pytest

from sensus_rag.contract import RetrievedChunk
from sensus_rag.errors import IngestionError, RerankUnavailable
from sensus_rag.reranking.fake import FakeReranker
from sensus_rag.retrieval import RerankingRetriever


def test_chunk_nasce_sem_diagnostico_de_rerank() -> None:
    """Os campos são aditivos: nenhum construtor existente precisa mudar, e
    `None` quer dizer 'este resultado não passou por rerank'."""
    chunk = RetrievedChunk(
        chunk_id="c1",
        document_id="d1",
        document_name="lei.md",
        content="texto",
        score=0.5,
    )

    assert chunk.rerank_score is None
    assert chunk.fused_rank is None


def test_rerank_indisponivel_e_falha_de_recuperacao() -> None:
    """Filha de `IngestionError`, não irmã: rerank é etapa de recuperação, e o
    catch-all de `rag_error_for` já devolve 503 para a família."""
    assert issubclass(RerankUnavailable, IngestionError)


def chunk(chunk_id: str, content: str, score: float = 0.5) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        document_id="d1",
        document_name="lei.md",
        content=content,
        score=score,
    )


async def test_fake_reordena_por_sobreposicao_de_palavras() -> None:
    fundidos = [
        chunk("c1", "assunto sem relação nenhuma"),
        chunk("c2", "prazo de entrega do produto"),
    ]

    ordenados = await FakeReranker().rerank(
        question="qual o prazo de entrega?", chunks=fundidos, top_k=2
    )

    assert [c.chunk_id for c in ordenados] == ["c2", "c1"]


async def test_fake_preenche_as_duas_ordens() -> None:
    """`fused_rank` guarda a posição de ENTRADA, que é a do RRF — sem ela, um
    reranker que devolvesse ordem aleatória sumiria sem deixar sinal."""
    fundidos = [
        chunk("c1", "assunto sem relação nenhuma"),
        chunk("c2", "prazo de entrega do produto"),
    ]

    ordenados = await FakeReranker().rerank(
        question="qual o prazo de entrega?", chunks=fundidos, top_k=2
    )

    assert [c.fused_rank for c in ordenados] == [2, 1]
    assert all(c.rerank_score is not None for c in ordenados)


async def test_fake_preserva_o_score_do_rrf() -> None:
    fundidos = [chunk("c1", "prazo de entrega", score=0.033)]

    ordenados = await FakeReranker().rerank(question="prazo", chunks=fundidos, top_k=1)

    assert ordenados[0].score == pytest.approx(0.033)


async def test_fake_corta_no_top_k() -> None:
    fundidos = [chunk(f"c{i}", f"trecho {i}") for i in range(5)]

    ordenados = await FakeReranker().rerank(question="trecho", chunks=fundidos, top_k=2)

    assert len(ordenados) == 2


async def test_fake_com_lista_vazia_devolve_vazio() -> None:
    assert await FakeReranker().rerank(question="p", chunks=[], top_k=5) == []


async def test_fake_e_deterministico_no_empate() -> None:
    """Empate resolve pela ordem do RRF. Sem isso, o duplo devolveria ordens
    diferentes entre execuções e nenhuma medição em cima dele significaria nada."""
    fundidos = [chunk("c1", "igual"), chunk("c2", "igual")]

    primeira = await FakeReranker().rerank(question="igual", chunks=fundidos, top_k=2)
    segunda = await FakeReranker().rerank(question="igual", chunks=fundidos, top_k=2)

    assert [c.chunk_id for c in primeira] == [c.chunk_id for c in segunda] == ["c1", "c2"]


@dataclass
class StubRetriever:
    """Registra com que `top_k` foi chamado — é o que prova que o decorador pede
    CANDIDATOS ao interno, e não o `top_k` final."""

    results: list[RetrievedChunk]
    asked_top_k: list[int] = field(default_factory=list)

    async def search(self, *, tenant_id: str, question: str, top_k: int) -> list[RetrievedChunk]:
        self.asked_top_k.append(top_k)
        return self.results[:top_k]


@dataclass
class BrokenReranker:
    async def rerank(
        self, *, question: str, chunks: Sequence[RetrievedChunk], top_k: int
    ) -> list[RetrievedChunk]:
        raise RerankUnavailable("provedor de rerank indisponível")


async def test_decorador_pede_candidatos_ao_interno_e_devolve_top_k() -> None:
    inner = StubRetriever(results=[chunk(f"c{i}", f"trecho {i}") for i in range(30)])
    retriever = RerankingRetriever(inner=inner, reranker=FakeReranker(), candidates=20)

    devolvidos = await retriever.search(tenant_id="t1", question="trecho", top_k=5)

    assert inner.asked_top_k == [20]
    assert len(devolvidos) == 5


async def test_decorador_nunca_pede_menos_que_o_top_k_solicitado() -> None:
    """Caso degenerado: `top_k` maior que `candidates`. Devolver menos do que foi
    pedido por causa de configuração seria bug silencioso."""
    inner = StubRetriever(results=[chunk(f"c{i}", f"trecho {i}") for i in range(60)])
    retriever = RerankingRetriever(inner=inner, reranker=FakeReranker(), candidates=20)

    devolvidos = await retriever.search(tenant_id="t1", question="trecho", top_k=50)

    assert inner.asked_top_k == [50]
    assert len(devolvidos) == 50


async def test_falha_do_rerank_cai_para_a_ordem_fundida(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """No chat, resposta um pouco pior é melhor que erro. No harness é o contrário —
    por isso o harness usa o `Reranker` direto, sem este decorador."""
    fundidos = [chunk("c1", "primeiro"), chunk("c2", "segundo")]
    inner = StubRetriever(results=fundidos)
    retriever = RerankingRetriever(inner=inner, reranker=BrokenReranker(), candidates=20)

    with caplog.at_level(logging.WARNING):
        devolvidos = await retriever.search(tenant_id="t1", question="p", top_k=2)

    assert [c.chunk_id for c in devolvidos] == ["c1", "c2"]
    assert all(c.rerank_score is None for c in devolvidos)
    assert "rerank" in caplog.text.lower()


@dataclass
class OrcamentoCurtoReranker:
    """Reordena e devolve só os primeiros `cabem` candidatos — é o que o
    `VoyageReranker` faz quando o corte de orçamento tira a cauda da lista."""

    cabem: int = 3

    async def rerank(
        self, *, question: str, chunks: Sequence[RetrievedChunk], top_k: int
    ) -> list[RetrievedChunk]:
        couberam = list(chunks)[: self.cabem]
        ordenados = [
            replace(candidato, rerank_score=1.0 - posicao / 10, fused_rank=len(couberam) - posicao)
            for posicao, candidato in enumerate(reversed(couberam))
        ]
        return ordenados[:top_k]


async def test_corte_de_orcamento_no_reranker_nao_encolhe_a_resposta() -> None:
    """O `FakeReranker` sempre devolve `top_k`, então a promessa do decorador nunca foi
    exercitada contra um reranker que devolve MENOS — que é o que o `VoyageReranker`
    faz quando a janela de tokens não comporta todos os candidatos. Sem o
    complemento, `/search` com `top_k=50` devolveria ~20 sem dizer por quê."""
    inner = StubRetriever(results=[chunk(f"c{i}", f"trecho {i}") for i in range(30)])
    retriever = RerankingRetriever(
        inner=inner, reranker=OrcamentoCurtoReranker(cabem=3), candidates=20
    )

    devolvidos = await retriever.search(tenant_id="t1", question="trecho", top_k=10)

    assert len(devolvidos) == 10
    # Os reordenados vêm na frente; a cauda completa na ordem do RRF, sem repetir.
    assert [c.chunk_id for c in devolvidos[:3]] == ["c2", "c1", "c0"]
    assert [c.chunk_id for c in devolvidos[3:]] == [f"c{i}" for i in range(3, 10)]
    assert len({c.chunk_id for c in devolvidos}) == 10


async def test_complemento_da_cauda_nao_finge_ter_passado_por_rerank() -> None:
    """`rerank_score` nulo é o que a rota ensina a ler como 'não passou por rerank' —
    preencher a cauda com score inventado corromperia esse diagnóstico."""
    inner = StubRetriever(results=[chunk(f"c{i}", f"trecho {i}") for i in range(30)])
    retriever = RerankingRetriever(
        inner=inner, reranker=OrcamentoCurtoReranker(cabem=3), candidates=20
    )

    devolvidos = await retriever.search(tenant_id="t1", question="trecho", top_k=10)

    cauda = devolvidos[3:]
    assert cauda, "sem cauda a asserção abaixo seria trivialmente verdadeira"
    assert all(c.rerank_score is None for c in cauda)
    assert all(c.rerank_score is not None for c in devolvidos[:3])

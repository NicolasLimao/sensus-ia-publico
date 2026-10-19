"""RRF em vez de soma ponderada: cosseno e ts_rank não são escalas comparáveis,
e posição é a única coisa que as duas pernas têm em comum."""

from sensus_rag.contract import ScoredChunk
from sensus_rag.fusion import reciprocal_rank_fusion


def chunk(chunk_id: str, score: float = 0.0) -> ScoredChunk:
    return ScoredChunk(
        chunk_id=chunk_id,
        document_id="doc",
        document_name="doc.txt",
        content=f"conteudo {chunk_id}",
        score=score,
    )


def test_chunk_bem_colocado_nas_duas_pernas_vence_o_primeiro_de_uma_so() -> None:
    result = reciprocal_rank_fusion(
        semantic=[chunk("a"), chunk("b")],
        lexical=[chunk("c"), chunk("b")],
        k=60,
        limit=10,
    )

    assert result[0].chunk_id == "b"


def test_escalas_incompativeis_nao_influenciam_o_resultado() -> None:
    # A perna semântica tem scores minúsculos, a lexical scores enormes. Numa soma
    # ponderada isso decidiria tudo; no RRF só a posição conta.
    result = reciprocal_rank_fusion(
        semantic=[chunk("a", 0.0001), chunk("b", 0.00005)],
        lexical=[chunk("a", 9999.0), chunk("b", 1.0)],
        k=60,
        limit=10,
    )

    assert [r.chunk_id for r in result] == ["a", "b"]


def test_deduplica_o_mesmo_chunk_vindo_das_duas_pernas() -> None:
    result = reciprocal_rank_fusion(semantic=[chunk("a")], lexical=[chunk("a")], k=60, limit=10)

    assert len(result) == 1
    assert result[0].score == 2 / 61


def test_respeita_o_limite() -> None:
    many = [chunk(str(i)) for i in range(10)]

    result = reciprocal_rank_fusion(semantic=many, lexical=[], k=60, limit=3)

    assert len(result) == 3


def test_lista_vazia_devolve_vazio() -> None:
    assert reciprocal_rank_fusion(semantic=[], lexical=[], k=60, limit=5) == []


def test_preserva_o_conteudo_e_o_documento_de_origem() -> None:
    result = reciprocal_rank_fusion(semantic=[chunk("a")], lexical=[], k=60, limit=5)

    assert result[0].content == "conteudo a"
    assert result[0].document_name == "doc.txt"


def test_registra_a_posicao_do_chunk_em_cada_perna() -> None:
    # SPEC §3.2: `RetrievedChunk` carrega as posições em cada perna, para diagnóstico.
    # Sem elas, uma perna quebrada some dentro do score fundido sem deixar sinal.
    result = reciprocal_rank_fusion(
        semantic=[chunk("a"), chunk("b")],
        lexical=[chunk("b"), chunk("a")],
        k=60,
        limit=10,
    )
    by_id = {r.chunk_id: r for r in result}

    assert (by_id["a"].semantic_rank, by_id["a"].lexical_rank) == (1, 2)
    assert (by_id["b"].semantic_rank, by_id["b"].lexical_rank) == (2, 1)


def test_perna_que_nao_devolveu_o_chunk_fica_com_posicao_nula() -> None:
    result = reciprocal_rank_fusion(
        semantic=[chunk("so-semantico")],
        lexical=[chunk("so-lexical")],
        k=60,
        limit=10,
    )
    by_id = {r.chunk_id: r for r in result}

    assert by_id["so-semantico"].semantic_rank == 1
    assert by_id["so-semantico"].lexical_rank is None
    assert by_id["so-lexical"].semantic_rank is None
    assert by_id["so-lexical"].lexical_rank == 1

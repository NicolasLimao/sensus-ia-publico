"""O chunker é o parâmetro que a fatia existe para medir — por isso é configurável."""

from sensus_rag.chunking import StructuralChunker


def test_texto_curto_vira_um_chunk_so() -> None:
    chunker = StructuralChunker(target_chars=200, overlap_chars=20)

    chunks = chunker.split("Um parágrafo curto.")

    assert len(chunks) == 1
    assert chunks[0].content == "Um parágrafo curto."
    assert chunks[0].index == 0


def test_quebra_em_paragrafos_ate_o_alvo() -> None:
    chunker = StructuralChunker(target_chars=60, overlap_chars=0)
    texto = "\n\n".join(["A" * 50, "B" * 50, "C" * 50])

    chunks = chunker.split(texto)

    assert len(chunks) == 3
    assert [c.index for c in chunks] == [0, 1, 2]


def test_paragrafos_pequenos_sao_agrupados_ate_o_alvo() -> None:
    chunker = StructuralChunker(target_chars=100, overlap_chars=0)
    texto = "\n\n".join(["A" * 30, "B" * 30, "C" * 30])

    chunks = chunker.split(texto)

    assert len(chunks) == 1
    assert "A" * 30 in chunks[0].content
    assert "C" * 30 in chunks[0].content


def test_offsets_apontam_para_o_texto_original() -> None:
    chunker = StructuralChunker(target_chars=60, overlap_chars=0)
    texto = "\n\n".join(["A" * 50, "B" * 50])

    chunks = chunker.split(texto)

    for chunk in chunks:
        assert texto[chunk.start : chunk.end] == chunk.content


def test_sobreposicao_repete_o_fim_do_chunk_anterior() -> None:
    chunker = StructuralChunker(target_chars=60, overlap_chars=10)
    texto = "\n\n".join(["A" * 50, "B" * 50])

    chunks = chunker.split(texto)

    assert chunks[1].content.startswith("A" * 10)


def test_paragrafo_maior_que_o_alvo_e_dividido() -> None:
    chunker = StructuralChunker(target_chars=40, overlap_chars=0)

    chunks = chunker.split("A" * 100)

    assert len(chunks) == 3
    assert all(len(c.content) <= 40 for c in chunks)


def test_texto_vazio_nao_produz_chunk() -> None:
    chunker = StructuralChunker(target_chars=100, overlap_chars=0)

    assert chunker.split("   \n\n  ") == []


def test_com_sobreposicao_os_offsets_delimitam_o_trecho_proprio() -> None:
    chunker = StructuralChunker(target_chars=60, overlap_chars=10)
    texto = "\n\n".join(["A" * 50, "B" * 50])

    chunks = chunker.split(texto)

    # `start`/`end` delimitam o trecho próprio, não incluindo a sobreposição
    assert texto[chunks[1].start : chunks[1].end] == "B" * 50
    # Mas o conteúdo começa com a cauda do chunk anterior
    assert chunks[1].content.startswith("A" * 10)
    # E termina com o trecho próprio
    assert chunks[1].content.endswith("B" * 50)

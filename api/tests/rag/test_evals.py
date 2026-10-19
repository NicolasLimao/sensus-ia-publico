"""Métricas e dataset. A ancoragem por trecho é o que faz o dataset sobreviver a
uma mudança de chunking — por isso tem teste próprio."""

from pathlib import Path

import pytest

from sensus_rag.contract import RetrievedChunk
from sensus_rag.evals.dataset import DatasetEntry, load_dataset
from sensus_rag.evals.metrics import hit_at, mean_reciprocal_rank, recall_at_k


def chunk_result(content: str, document: str = "manual.pdf") -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=f"id-{content}",
        document_id="doc",
        document_name=document,
        content=content,
        score=1.0,
    )


def test_acerto_quando_o_chunk_contem_o_trecho_anotado() -> None:
    entrada = DatasetEntry(
        pergunta="qual o prazo?",
        documento="manual.pdf",
        trecho_esperado="em até 30 dias",
    )
    resultados = [chunk_result("O pedido deve ser feito em até 30 dias corridos.")]

    assert hit_at(entrada, resultados) == 1


def test_acerto_ignora_caixa_e_espaco_repetido() -> None:
    entrada = DatasetEntry("q", "manual.pdf", "EM ATÉ   30 dias")
    resultados = [chunk_result("feito em até 30 dias.")]

    assert hit_at(entrada, resultados) == 1


def test_o_documento_errado_nao_conta_como_acerto() -> None:
    entrada = DatasetEntry("q", "manual.pdf", "em até 30 dias")
    resultados = [chunk_result("em até 30 dias", document="outro.pdf")]

    assert hit_at(entrada, resultados) is None


def test_sem_acerto_devolve_none() -> None:
    entrada = DatasetEntry("q", "manual.pdf", "em até 30 dias")

    assert hit_at(entrada, [chunk_result("texto sem relação")]) is None


def test_acerto_devolve_a_posicao_um_indexada() -> None:
    entrada = DatasetEntry("q", "manual.pdf", "trinta dias")
    resultados = [
        chunk_result("nada"),
        chunk_result("nada tambem"),
        chunk_result("trinta dias"),
    ]

    assert hit_at(entrada, resultados) == 3


def test_recall_at_k_conta_so_o_que_caiu_ate_a_posicao_k() -> None:
    assert recall_at_k([1, 3, None, 7], k=3) == pytest.approx(0.5)


def test_recall_at_k_de_dataset_vazio_e_zero() -> None:
    assert recall_at_k([], k=5) == 0.0


def test_mrr_usa_o_inverso_da_posicao() -> None:
    assert mean_reciprocal_rank([1, 2, None]) == pytest.approx((1.0 + 0.5 + 0.0) / 3)


def test_dataset_e_carregado_do_yaml(tmp_path: Path) -> None:
    arquivo = tmp_path / "dataset.yaml"
    arquivo.write_text(
        "- pergunta: Qual o prazo?\n  documento: manual.pdf\n  trecho_esperado: em até 30 dias\n",
        encoding="utf-8",
    )

    entradas = load_dataset(arquivo)

    assert entradas == [DatasetEntry("Qual o prazo?", "manual.pdf", "em até 30 dias")]


def test_dataset_com_campo_faltando_falha_alto(tmp_path: Path) -> None:
    arquivo = tmp_path / "dataset.yaml"
    arquivo.write_text("- pergunta: Sem trecho\n  documento: manual.pdf\n", encoding="utf-8")

    with pytest.raises(ValueError, match="trecho_esperado"):
        load_dataset(arquivo)


def test_dataset_yaml_topo_nao_lista_falha(tmp_path: Path) -> None:
    arquivo = tmp_path / "dataset.yaml"
    arquivo.write_text("pergunta: oops\n", encoding="utf-8")

    with pytest.raises(ValueError, match="deve ser uma lista"):
        load_dataset(arquivo)


def test_dataset_yaml_entrada_escalar_falha(tmp_path: Path) -> None:
    arquivo = tmp_path / "dataset.yaml"
    arquivo.write_text('- "string simples"\n', encoding="utf-8")

    with pytest.raises(ValueError, match="deve ser um mapeamento"):
        load_dataset(arquivo)

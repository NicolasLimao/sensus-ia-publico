"""O dataset de perguntas sem resposta é o instrumento que mede alucinação.

Um dataset malformado aqui produziria uma taxa de recusa que parece válida e não é —
o pior resultado possível para uma medição.
"""

from pathlib import Path

import pytest

from sensus_rag.evals.dataset import UNANSWERED_TYPES, load_unanswerable

DATASET = Path(__file__).resolve().parents[2] / "evals" / "dataset_sem_resposta.yaml"


def test_o_arquivo_carrega_e_tem_as_duas_familias() -> None:
    entries = load_unanswerable(DATASET)

    tipos = {entry.tipo for entry in entries}
    assert tipos == UNANSWERED_TYPES
    assert len(entries) == 12


def test_toda_pergunta_e_unica() -> None:
    perguntas = [entry.pergunta for entry in load_unanswerable(DATASET)]

    assert len(set(perguntas)) == len(perguntas)


def test_tipo_desconhecido_e_recusado(tmp_path: Path) -> None:
    arquivo = tmp_path / "ruim.yaml"
    arquivo.write_text('- pergunta: "e?"\n  tipo: "inventado"\n', encoding="utf-8")

    with pytest.raises(ValueError, match="tipo"):
        load_unanswerable(arquivo)


def test_campo_faltando_e_recusado(tmp_path: Path) -> None:
    arquivo = tmp_path / "ruim.yaml"
    arquivo.write_text('- pergunta: "e?"\n', encoding="utf-8")

    with pytest.raises(ValueError, match="sem"):
        load_unanswerable(arquivo)


def test_topo_nao_lista_e_recusado(tmp_path: Path) -> None:
    arquivo = tmp_path / "ruim.yaml"
    arquivo.write_text('pergunta: "e?"\ntipo: "ausente_no_dominio"\n', encoding="utf-8")

    with pytest.raises(ValueError, match="lista"):
        load_unanswerable(arquivo)

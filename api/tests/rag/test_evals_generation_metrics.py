"""Taxa de recusa e taxa de resposta. As duas só valem em par."""

from sensus_rag.evals.metrics import rate


def test_taxa_de_lista_vazia_e_zero() -> None:
    assert rate([]) == 0.0


def test_taxa_conta_os_verdadeiros() -> None:
    assert rate([True, True, False, True]) == 0.75


def test_taxa_perfeita() -> None:
    assert rate([True, True]) == 1.0

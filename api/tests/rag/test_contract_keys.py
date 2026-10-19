"""Guarda de regressão das chaves dos testes de contrato.

Os testes de contrato não rodam na suíte padrão (`-m contract`), então nada os impede
de voltar a ler `os.environ` sem que ninguém perceba — e foi assim que os três ficaram
PULANDO numa máquina configurada por `api/.env`, que é onde eles mais precisavam rodar.
Este teste roda na suíte padrão e é o que percebe.

A checagem é no código, não no texto: um AST, e não uma busca por substring, para que
esta própria linha de prosa citando `os.environ` não a dispare.
"""

import ast
from pathlib import Path

import pytest

CONTRATOS = ("test_contract_openai.py", "test_contract_voyage_rerank.py")


def _le_o_ambiente(fonte: str) -> bool:
    arvore = ast.parse(fonte)
    for no in ast.walk(arvore):
        if (
            isinstance(no, ast.Attribute)
            and no.attr in {"environ", "getenv"}
            and isinstance(no.value, ast.Name)
            and no.value.id == "os"
        ):
            return True
        if (
            isinstance(no, ast.ImportFrom)
            and no.module == "os"
            and any(alias.name in {"environ", "getenv"} for alias in no.names)
        ):
            return True
    return False


@pytest.mark.parametrize("nome", CONTRATOS)
def test_teste_de_contrato_nao_le_o_ambiente_direto(nome: str) -> None:
    """A chave vem das fixtures de `tests/conftest.py`, que leem as MESMAS settings da
    aplicação. `os.environ` não enxerga o `.env` (pydantic-settings lê o arquivo, não
    exporta), então lê-lo faz o teste pular exatamente onde deveria rodar."""
    fonte = (Path(__file__).parent / nome).read_text(encoding="utf-8")

    assert not _le_o_ambiente(fonte), (
        f"{nome} voltou a ler o ambiente direto — use as fixtures de chave, "
        "senão o contrato pula numa máquina configurada por .env"
    )

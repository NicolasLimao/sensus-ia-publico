"""`sensus_rag` não pode conhecer `sensus_api`. A regra é do projeto, então é testada."""

import ast
from pathlib import Path

FONTE = Path(__file__).resolve().parents[2] / "src" / "sensus_rag"


def test_sensus_rag_nunca_importa_sensus_api() -> None:
    offenders: list[str] = []
    for file in FONTE.rglob("*.py"):
        tree = ast.parse(file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("sensus_api"):
                offenders.append(f"{file.name}:{node.lineno}")
            if isinstance(node, ast.Import):
                offenders += [
                    f"{file.name}:{node.lineno}"
                    for alias in node.names
                    if alias.name.startswith("sensus_api")
                ]
    assert offenders == []

"""Dataset anotado à mão.

Cada entrada aponta para um TRECHO DE TEXTO, não para um id de chunk. Id de chunk muda
toda vez que o chunking muda, e um dataset que se invalida a cada experimento não serve
para comparar experimentos.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

FIELDS = ("pergunta", "documento", "trecho_esperado")


@dataclass(frozen=True)
class DatasetEntry:
    pergunta: str
    documento: str
    trecho_esperado: str


def _require_list(raw: Any) -> list[Any]:
    """Valida que o topo do YAML carregado é uma lista, comum aos dois carregadores."""
    if not isinstance(raw, list):
        raise ValueError(f"Dataset deve ser uma lista YAML, recebeu {type(raw).__name__}")
    return raw


def _require_mapping(entry: Any, position: int) -> dict[Any, Any]:
    """Valida que uma entrada da lista é um mapeamento, comum aos dois carregadores."""
    if not isinstance(entry, dict):
        raise ValueError(
            f"Entrada {position} do dataset deve ser um mapeamento, recebeu {type(entry).__name__}"
        )
    return entry


def load_dataset(path: Path) -> list[DatasetEntry]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))

    entries: list[DatasetEntry] = []
    for position, entry in enumerate(_require_list(raw), start=1):
        entry = _require_mapping(entry, position)

        missing = [field for field in FIELDS if not entry.get(field)]
        if missing:
            raise ValueError(f"Entrada {position} do dataset sem: {', '.join(missing)}")

        entries.append(
            DatasetEntry(
                pergunta=str(entry["pergunta"]),
                documento=str(entry["documento"]),
                trecho_esperado=str(entry["trecho_esperado"]),
            )
        )
    return entries


UNANSWERED_FIELDS = ("pergunta", "tipo")

UNANSWERED_TYPES = frozenset({"ausente_no_dominio", "fora_do_dominio"})


@dataclass(frozen=True)
class UnansweredEntry:
    """Pergunta cuja resposta NÃO está no corpus. Não tem âncora — de propósito."""

    pergunta: str
    tipo: str


def load_unanswerable(path: Path) -> list[UnansweredEntry]:
    """Carrega o dataset de perguntas sem resposta.

    Separado de `load_dataset` porque as duas medem coisas diferentes: `recall_at_k`
    contaria uma pergunta sem resposta como erro de recuperação, rebaixando a linha de
    base sem que nada estivesse errado.
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))

    entries: list[UnansweredEntry] = []
    for position, entry in enumerate(_require_list(raw), start=1):
        entry = _require_mapping(entry, position)

        missing = [field for field in UNANSWERED_FIELDS if not entry.get(field)]
        if missing:
            raise ValueError(f"Entrada {position} do dataset sem: {', '.join(missing)}")

        tipo = str(entry["tipo"])
        if tipo not in UNANSWERED_TYPES:
            raise ValueError(
                f"Entrada {position} com tipo desconhecido: {tipo!r}. "
                f"Use um de: {', '.join(sorted(UNANSWERED_TYPES))}"
            )

        entries.append(UnansweredEntry(pergunta=str(entry["pergunta"]), tipo=tipo))
    return entries

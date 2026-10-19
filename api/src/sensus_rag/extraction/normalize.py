"""Normalização do texto cru — uma função por origem, porque o ruído é diferente.

No PDF, a quebra de linha é acidente de diagramação: a frase continua na linha
seguinte, palavras são partidas com hífen no fim da linha e o espaçamento vem
duplicado. Juntar tudo é a leitura certa.

No texto puro e no Markdown, a quebra de linha é do autor. Título, item de lista e
linha de tabela vivem cada um na sua linha, e é essa estrutura que torna o trecho
recuperável depois. Aplicar a normalização de PDF a um `.md` — formato que aceitamos —
transformava o documento inteiro em linha corrida e envenenava a medição.
"""

import re

_HYPHEN_AT_LINE_END = re.compile(r"(\w)-\n(\w)")
_PARAGRAPH_BOUNDARY = re.compile(r"\n\s*\n")
_SIMPLE_LINE_BREAK = re.compile(r"(?<!\n)\n(?!\n)")
_SPACES = re.compile(r"[ \t]+")
_LINE_ENDINGS = re.compile(r"\r\n?")
_BLANK_LINE_RUN = re.compile(r"\n{3,}")


def normalize_pdf(text: str) -> str:
    """Junta a hifenização, colapsa quebras simples em espaço e preserva parágrafos."""
    result = _HYPHEN_AT_LINE_END.sub(r"\1\2", text)
    result = _PARAGRAPH_BOUNDARY.sub("\n\n", result)
    result = _SIMPLE_LINE_BREAK.sub(" ", result)
    result = _SPACES.sub(" ", result)
    return result.strip()


def normalize_plain(text: str) -> str:
    """Limpa o ruído sem tocar na estrutura.

    Colapsa espaço repetido no meio da linha, apara o fim dela e reduz sequências de
    linhas em branco a uma só. Não junta quebras de linha e não mexe na indentação —
    lista aninhada e bloco de código dependem dela.
    """
    lines = []
    for raw in _LINE_ENDINGS.sub("\n", text).split("\n"):
        indent = raw[: len(raw) - len(raw.lstrip(" \t"))]
        lines.append((indent + _SPACES.sub(" ", raw.strip())).rstrip())
    return _BLANK_LINE_RUN.sub("\n\n", "\n".join(lines)).strip()

"""Extração e normalização. PDF sem camada de texto tem de falhar alto, não em silêncio."""

import io

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from sensus_rag.errors import EmptyDocument, UnsupportedDocument
from sensus_rag.extraction.detect import extractor_for
from sensus_rag.extraction.normalize import normalize_pdf, normalize_plain
from sensus_rag.extraction.pdf import PdfExtractor
from sensus_rag.extraction.plain import PlainTextExtractor


def pdf_with_text(lines: list[str]) -> bytes:
    buffer = io.BytesIO()
    page = canvas.Canvas(buffer, pagesize=A4)
    for i, line in enumerate(lines):
        page.drawString(72, 780 - i * 16, line)
    page.save()
    return buffer.getvalue()


def pdf_without_text() -> bytes:
    buffer = io.BytesIO()
    page = canvas.Canvas(buffer, pagesize=A4)
    page.rect(72, 700, 200, 80, fill=1)  # só um retângulo: nenhuma camada de texto
    page.save()
    return buffer.getvalue()


def test_texto_puro_e_decodificado() -> None:
    extractor = PlainTextExtractor()

    assert extractor.extract("Olá, mundo.".encode(), "nota.txt") == "Olá, mundo."


def test_texto_puro_com_bytes_invalidos_nao_estoura() -> None:
    extractor = PlainTextExtractor()

    result = extractor.extract(b"caf\xe9 quente", "nota.txt")

    assert "quente" in result


def test_pdf_com_camada_de_texto_e_extraido() -> None:
    extractor = PdfExtractor()

    result = extractor.extract(pdf_with_text(["O prazo e de 30 dias."]), "manual.pdf")

    assert "30 dias" in result


def test_pdf_sem_camada_de_texto_levanta_empty_document() -> None:
    extractor = PdfExtractor()

    with pytest.raises(EmptyDocument):
        extractor.extract(pdf_without_text(), "escaneado.pdf")


def test_pdf_ilegivel_levanta_unsupported_document() -> None:
    extractor = PdfExtractor()

    with pytest.raises(UnsupportedDocument):
        extractor.extract(b"isto nao e um pdf", "quebrado.pdf")


def test_normalize_pdf_junta_hifenizacao_de_fim_de_linha() -> None:
    assert normalize_pdf("reembol-\nso integral") == "reembolso integral"


def test_normalize_pdf_preserva_paragrafos_e_colapsa_espacos() -> None:
    assert normalize_pdf("um   dois\n\n\n\ntres") == "um dois\n\ntres"


def test_normalize_pdf_preserva_paragrafos_com_espacos_em_branco() -> None:
    assert normalize_pdf("paragrafo um\n \nparagrafo dois") == "paragrafo um\n\nparagrafo dois"


def test_normalize_pdf_nao_corrompe_conteudo_literal() -> None:
    # Regressão: garantir que strings literais no documento não são substituídas
    # por placeholders. A normalização usa apenas regex, nunca texto puro.
    text = "Documentacao: use a constante __PARAGRAPH_BREAK__ no codigo."
    assert normalize_pdf(text) == text


def test_normalize_plain_preserva_a_estrutura_de_markdown() -> None:
    """`.md` é formato suportado, e a normalização de PDF o destruía.

    Juntar quebra de linha simples é certo no PDF, onde a quebra é acidente de
    diagramação. No Markdown ela é semântica: título, item de lista e linha de tabela
    viravam uma linha corrida, e o chunk perdia a estrutura que o torna recuperável —
    contaminando a linha de base que a fatia ainda vai medir.
    """
    markdown = (
        "# Politica de reembolso\n"
        "\n"
        "- Prazo: 30 dias\n"
        "- Canal: portal do cliente\n"
        "\n"
        "| Regiao | Prazo |\n"
        "| --- | --- |\n"
        "| Sul | 30 dias |"
    )

    assert normalize_plain(markdown) == markdown


def test_normalize_plain_preserva_a_indentacao_de_lista_aninhada() -> None:
    texto = "- item\n    - subitem\n        - subsubitem"

    assert normalize_plain(texto) == texto


def test_normalize_plain_colapsa_espaco_interno_e_apara_o_fim_da_linha() -> None:
    assert normalize_plain("um   dois   \ntres") == "um dois\ntres"


def test_normalize_plain_colapsa_linhas_em_branco_repetidas() -> None:
    assert normalize_plain("um\n\n\n\n\ndois") == "um\n\ndois"


def test_normalize_plain_normaliza_fim_de_linha_do_windows() -> None:
    assert normalize_plain("um\r\ndois") == "um\ndois"


def test_normalize_plain_nao_junta_a_hifenizacao_de_fim_de_linha() -> None:
    # No texto puro o hífen no fim da linha é do autor, não da diagramação:
    # "bem-\nestar" é palavra composta quebrada, e juntá-la sem o hífen a corrompe.
    assert normalize_plain("bem-\nestar") == "bem-\nestar"


def test_extrator_de_markdown_preserva_o_titulo() -> None:
    # A costura: o extrator de .md tem de usar `normalize_plain`, não o de PDF.
    extractor = PlainTextExtractor()

    result = extractor.extract(b"# Titulo\n\nCorpo do texto.", "politica.md")

    assert result == "# Titulo\n\nCorpo do texto."


def test_detect_escolhe_por_content_type() -> None:
    assert isinstance(extractor_for("application/pdf", "a.pdf"), PdfExtractor)
    assert isinstance(extractor_for("text/markdown", "a.md"), PlainTextExtractor)


def test_detect_cai_para_a_extensao_quando_o_content_type_e_generico() -> None:
    assert isinstance(extractor_for("application/octet-stream", "a.pdf"), PdfExtractor)


def test_detect_recusa_formato_desconhecido() -> None:
    with pytest.raises(UnsupportedDocument):
        extractor_for("application/vnd.ms-excel", "planilha.xlsx")

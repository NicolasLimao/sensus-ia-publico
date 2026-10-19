"""O dataset de medição tem de ser ALCANÇÁVEL, não só bem formado.

Uma âncora que não existe no documento, ou que cai exatamente entre dois chunks, é uma
pergunta que **nenhuma** recuperação acerta — nem uma perfeita. Ela rebaixa recall e MRR
sem dar sinal, e a linha de base passa a medir o dataset em vez de medir o motor. Como a
linha de base é a referência contra a qual toda mudança de chunking e recuperação será
comparada, um dataset impossível contamina todas as decisões seguintes.

O teste usa a função de acerto de produção (`hit_at`), não uma comparação própria: o que
interessa é se o harness contaria como acerto, não se as strings se parecem.

Roda na suíte padrão — só lê arquivos, não toca banco nem rede.
"""

from pathlib import Path

import pytest

from sensus_rag.chunking import StructuralChunker
from sensus_rag.config import RagSettings
from sensus_rag.contract import RetrievedChunk
from sensus_rag.evals.dataset import DatasetEntry, load_dataset
from sensus_rag.evals.metrics import hit_at
from sensus_rag.extraction.detect import extractor_for

API = Path(__file__).resolve().parents[2]
CORPUS = API / "evals" / "corpus"
DATASET = API / "evals" / "dataset.yaml"

CONTENT_TYPES = {".pdf": "application/pdf", ".md": "text/markdown", ".txt": "text/plain"}

# Os defaults DECLARADOS, não os do ambiente: a linha de base é registrada nesta
# configuração, então é nela que as âncoras precisam caber.
_DEFAULTS = RagSettings.model_fields
TARGET_CHARS = int(_DEFAULTS["chunk_target_chars"].default)
OVERLAP_CHARS = int(_DEFAULTS["chunk_overlap_chars"].default)


def corpus_files() -> list[Path]:
    if not CORPUS.is_dir():
        return []
    return sorted(p for p in CORPUS.iterdir() if p.is_file() and p.name != ".gitkeep")


def load_entries() -> list[DatasetEntry]:
    if not DATASET.is_file():
        return []
    entries = load_dataset(DATASET)
    # O arquivo versionado nasce com uma entrada de exemplo; ela não é corpus real.
    return [e for e in entries if not e.documento.startswith("nome-do-arquivo")]


ENTRIES = load_entries()
FILES = corpus_files()
HAS_CORPUS = bool(ENTRIES) and bool(FILES)

pytestmark = pytest.mark.skipif(
    not HAS_CORPUS, reason="sem corpus ou dataset anotado — ver evals/README.md"
)


def chunks_of(name: str) -> list[RetrievedChunk]:
    """Todos os chunks do documento, como se a recuperação tivesse devolvido todos.

    É o melhor caso possível: se `hit_at` não acha a âncora aqui, nenhuma recuperação
    acha em lugar nenhum.
    """
    path = CORPUS / name
    extractor = extractor_for(CONTENT_TYPES.get(path.suffix.lower(), ""), name)
    text = extractor.extract(path.read_bytes(), name)
    chunker = StructuralChunker(target_chars=TARGET_CHARS, overlap_chars=OVERLAP_CHARS)
    return [
        RetrievedChunk(
            chunk_id=str(c.index),
            document_id="doc",
            document_name=name,
            content=c.content,
            score=0.0,
        )
        for c in chunker.split(text)
    ]


def test_todo_documento_citado_existe_no_corpus() -> None:
    disponiveis = {p.name for p in FILES}
    faltando = sorted({e.documento for e in ENTRIES} - disponiveis)

    assert faltando == [], f"dataset cita documento que não está no corpus: {faltando}"


@pytest.mark.parametrize("entry", ENTRIES, ids=lambda e: e.pergunta[:60])
def test_a_ancora_cabe_inteira_em_algum_chunk(entry: DatasetEntry) -> None:
    """Se falhar, a âncora está partida entre dois chunks ou não existe no documento.

    Conserto: encurtar o `trecho_esperado` para uma frase dentro de um só parágrafo.
    """
    assert hit_at(entry, chunks_of(entry.documento)) is not None


def test_nenhuma_pergunta_repetida() -> None:
    perguntas = [e.pergunta for e in ENTRIES]

    assert len(perguntas) == len(set(perguntas))


def test_o_dataset_tem_o_tamanho_que_o_readme_promete() -> None:
    # 20 a 30: abaixo disso uma pergunta sozinha move o recall demais para o número
    # significar algo; acima, o custo de anotar à mão deixa de compensar nesta fase.
    assert 20 <= len(ENTRIES) <= 30


def test_todo_documento_do_corpus_tem_pergunta() -> None:
    """Documento indexado que nenhuma pergunta alcança só adiciona ruído à recuperação
    — ele compete pelos candidatos sem nunca poder acertar."""
    citados = {e.documento for e in ENTRIES}
    orfaos = sorted(p.name for p in FILES if p.name not in citados)

    assert orfaos == [], f"documento no corpus sem nenhuma pergunta: {orfaos}"

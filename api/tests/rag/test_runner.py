"""Mapeamento de content-type do runner de medição — função pura, sem dependência de
rede ou banco, então tem teste próprio mesmo fora do pytest de propósito do runner.

Os testes de `_answer` e `measure_generation` abaixo cobrem a costura "busca → gera" do
lado da medição — a mesma que `RagUpstream.ask` cobre do lado de produção (SPEC §3.1,
mitigação da divergência entre os dois). Nenhum toca rede: `StubRetriever` e
`ScriptedGenerator` são os mesmos duplos que provam o comportamento do adaptador.
"""

import sys
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest

from sensus_rag.config import RagSettings, get_rag_settings
from sensus_rag.contract import (
    AnswerRefusal,
    AnswerToken,
    GenerationConfig,
    GenerationEvent,
    RetrievedChunk,
)
from sensus_rag.errors import GenerationUnavailable
from sensus_rag.evals.dataset import DatasetEntry
from sensus_rag.evals.metrics import hit_at
from sensus_rag.evals.runner import _answer, _content_type, measure_generation, measure_retrieval
from sensus_rag.generation.fake import CONFIG_DE_DUPLO, ScriptedGenerator
from sensus_rag.reranking.fake import FakeReranker


@pytest.mark.parametrize(
    ("nome", "esperado"),
    [
        ("manual.pdf", "application/pdf"),
        ("manual.PDF", "application/pdf"),
        ("notas.md", "text/markdown"),
        ("notas.txt", "text/plain"),
        ("dados.csv", "application/octet-stream"),
    ],
)
def test_content_type_por_extensao(nome: str, esperado: str) -> None:
    assert _content_type(Path(nome)) == esperado


def test_generation_exige_chave_e_modelo(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Falhar cedo e com mensagem clara é melhor que indexar o corpus inteiro e só
    então descobrir que falta a chave — a indexação custa cota."""
    from sensus_rag.evals import runner

    monkeypatch.setattr(sys, "argv", ["runner", "--generation"])
    monkeypatch.setenv("SENSUS_DATABASE_URL", "postgresql://x")
    monkeypatch.setenv("SENSUS_VOYAGE_API_KEY", "k")
    monkeypatch.setenv("SENSUS_OPENAI_API_KEY", "")
    get_rag_settings.cache_clear()
    try:
        assert runner.main() == 1
        assert "SENSUS_OPENAI_API_KEY" in capsys.readouterr().err
    finally:
        get_rag_settings.cache_clear()


FOUND = [
    RetrievedChunk(
        chunk_id="c1",
        document_id="d1",
        document_name="cdc.md",
        content="o prazo é de sete dias",
        score=0.4,
    )
]


class StubRetriever:
    def __init__(self, results: list[RetrievedChunk]) -> None:
        self.results = results

    async def search(self, *, tenant_id: str, question: str, top_k: int) -> list[RetrievedChunk]:
        return self.results


async def test_answer_busca_vazia_recusa_sem_chamar_o_gerador() -> None:
    """Mesma regra do `RagUpstream.ask`: base vazia não gasta chamada de geração."""
    generator = ScriptedGenerator([AnswerToken("nunca deveria sair")])

    text, refused = await _answer(
        StubRetriever([]),
        generator,
        tenant_id="t1",
        question="p?",
        top_k=5,
        config=CONFIG_DE_DUPLO,
    )

    assert (text, refused) == ("", True)
    assert generator.calls == []


async def test_answer_recusa_do_gerador_vira_refused_true() -> None:
    """Mesma regra do `RagUpstream.ask`: `AnswerRefusal` vira `refused=True`."""
    generator = ScriptedGenerator([AnswerRefusal()])

    text, refused = await _answer(
        StubRetriever(FOUND),
        generator,
        tenant_id="t1",
        question="p?",
        top_k=5,
        config=CONFIG_DE_DUPLO,
    )

    assert (text, refused) == ("", True)


async def test_answer_tokens_saem_concatenados_na_ordem() -> None:
    """Mesma regra do `RagUpstream.ask`: os tokens saem concatenados, na ordem."""
    generator = ScriptedGenerator([AnswerToken("Sete "), AnswerToken("dias.")])

    text, refused = await _answer(
        StubRetriever(FOUND),
        generator,
        tenant_id="t1",
        question="p?",
        top_k=5,
        config=CONFIG_DE_DUPLO,
    )

    assert (text, refused) == ("Sete dias.", False)


class FlakyGenerator:
    """Recusa a segunda chamada com `GenerationUnavailable`; as demais respondem."""

    def __init__(self) -> None:
        self.calls = 0

    async def generate(
        self,
        *,
        question: str,
        chunks: Sequence[RetrievedChunk],
        config: GenerationConfig,
    ) -> AsyncIterator[GenerationEvent]:
        self.calls += 1
        if self.calls == 2:
            raise GenerationUnavailable("caiu")
        yield AnswerToken("ok")


async def test_measure_generation_isola_a_falha_de_uma_pergunta_e_termina_a_rodada(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pergunta 2 de 3 falha no provedor: a rodada não pode morrer com traceback e
    perder as respostas das perguntas 1 e 3, já pagas (I4)."""
    from sensus_rag.evals import runner

    written: list[tuple[str, str, bool]] = []
    monkeypatch.setattr(runner, "load_unanswerable", lambda _path: [])
    monkeypatch.setattr(runner, "_write_answers", lambda answers: written.extend(answers))

    answered = [
        DatasetEntry(pergunta="p1", documento="d.md", trecho_esperado="x"),
        DatasetEntry(pergunta="p2", documento="d.md", trecho_esperado="x"),
        DatasetEntry(pergunta="p3", documento="d.md", trecho_esperado="x"),
    ]

    await measure_generation(
        StubRetriever(FOUND),
        FlakyGenerator(),
        RagSettings(),
        tenant_id="t1",
        answered=answered,
    )

    assert [pergunta for pergunta, _, _ in written] == ["p1", "p3"]


@dataclass
class CountingRetriever:
    """Conta buscas. É o que prova que as duas ordens saem de UMA chamada — se saíssem
    de duas, o A/B voltaria a comparar coisas colhidas em momentos diferentes, que é o
    ruído que esta fatia existe para eliminar."""

    results: list[RetrievedChunk]
    calls: int = 0

    async def search(self, *, tenant_id: str, question: str, top_k: int) -> list[RetrievedChunk]:
        self.calls += 1
        return self.results[:top_k]


def _chunk(chunk_id: str, content: str) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        document_id="d1",
        document_name="lei.md",
        content=content,
        score=0.5,
    )


async def test_ab_sai_de_uma_unica_busca_por_pergunta() -> None:
    entries = [
        DatasetEntry(
            pergunta="qual o prazo de entrega?",
            documento="lei.md",
            trecho_esperado="prazo de entrega",
        )
    ]
    retriever = CountingRetriever(
        results=[_chunk("c1", "assunto qualquer"), _chunk("c2", "prazo de entrega")]
    )

    fundido, reordenado = await measure_retrieval(
        retriever, entries, tenant_id="t1", reranker=FakeReranker()
    )

    assert retriever.calls == 1
    assert fundido == [2]
    assert reordenado == [1]


async def test_sem_reranker_so_sai_a_coluna_fundida() -> None:
    entries = [
        DatasetEntry(pergunta="prazo", documento="lei.md", trecho_esperado="prazo de entrega")
    ]
    retriever = CountingRetriever(results=[_chunk("c1", "prazo de entrega")])

    fundido, reordenado = await measure_retrieval(retriever, entries, tenant_id="t1", reranker=None)

    assert fundido == [1]
    assert reordenado is None


def test_rerank_com_generation_e_recusado(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Somar as duas seria ~38 chamadas de rerank a ~1/min em cima dos ~36 min da
    geração. Fidelidade não é o critério desta fatia."""
    from sensus_rag.evals import runner

    monkeypatch.setattr(sys, "argv", ["runner", "--rerank", "--generation"])
    monkeypatch.setattr(
        runner,
        "get_rag_settings",
        lambda: RagSettings(
            database_url="postgres://x",
            voyage_api_key="k",
            openai_api_key="k",
            generation_model="gpt-4o-mini",
            rerank_model="rerank-2.5",
        ),
    )

    assert runner.main() == 1
    assert "--rerank" in capsys.readouterr().err


async def test_a_coluna_fundida_le_so_a_janela_das_metricas() -> None:
    """A busca pede `candidates` resultados — mais do que as métricas leem.

    Sem a fatia `found[: max(KS)]`, um acerto na posição 15 contaria como 1/15 em vez
    de `None`, e a coluna fundida deixaria de ser comparável à linha de base do SDD,
    que foi medida sobre max(KS) = 10 resultados. É a linha mais consequente do A/B:
    é ela que mantém as duas colunas medindo a mesma janela.
    """
    entries = [
        DatasetEntry(
            pergunta="qual o prazo de entrega?",
            documento="lei.md",
            trecho_esperado="prazo de entrega",
        )
    ]
    results = [_chunk(f"c{i}", "assunto qualquer") for i in range(14)]
    results.append(_chunk("c15", "prazo de entrega"))
    retriever = CountingRetriever(results=results)

    # Sem a fatia o acerto existe e seria contado: é isso que torna o `None` abaixo
    # falsificável em vez de trivialmente verdadeiro.
    assert hit_at(entries[0], results) == 15

    fundido, _ = await measure_retrieval(
        retriever, entries, tenant_id="t1", reranker=None, candidates=20
    )

    assert fundido == [None]


@dataclass
class OrcamentoCurtoReranker:
    """Devolve menos candidatos do que recebeu — é o que o `VoyageReranker` faz quando
    a janela de tokens não comporta a lista inteira."""

    cabem: int = 2

    async def rerank(
        self, *, question: str, chunks: Sequence[RetrievedChunk], top_k: int
    ) -> list[RetrievedChunk]:
        return list(chunks)[: self.cabem]


async def test_harness_avisa_quando_a_reordenada_fica_menor_que_a_janela(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Com menos de max(KS) candidatos reordenados, a coluna do rerank não TEM como
    acertar em @10 — o A/B vira contra o rerank por artefato de configuração, não por
    qualidade. Uma rodada de ~26 min não pode terminar com isso invisível."""
    entries = [
        DatasetEntry(
            pergunta="qual o prazo de entrega?",
            documento="lei.md",
            trecho_esperado="prazo de entrega",
        )
    ]
    retriever = CountingRetriever(results=[_chunk(f"c{i}", "assunto qualquer") for i in range(20)])

    await measure_retrieval(
        retriever,
        entries,
        tenant_id="t1",
        reranker=OrcamentoCurtoReranker(cabem=2),
        candidates=20,
    )

    erro = capsys.readouterr().err
    assert "2" in erro and "prazo de entrega" in erro


async def test_harness_nao_avisa_quando_o_corpus_e_que_e_curto(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Lista curta porque só havia 3 resultados não é corte de orçamento — avisar aqui
    treinaria quem roda a ignorar o aviso que importa."""
    entries = [
        DatasetEntry(pergunta="prazo", documento="lei.md", trecho_esperado="prazo de entrega")
    ]
    retriever = CountingRetriever(results=[_chunk("c1", "prazo de entrega")])

    await measure_retrieval(
        retriever, entries, tenant_id="t1", reranker=FakeReranker(), candidates=20
    )

    assert capsys.readouterr().err == ""


UUID_DO_EVAL = "11111111-1111-1111-1111-111111111111"


class CursorFalso:
    def __init__(self, row: tuple[str, ...] | None) -> None:
        self.row = row

    async def fetchone(self) -> tuple[str, ...] | None:
        return self.row


class ConexaoFalsa:
    def __init__(self, row: tuple[str, ...] | None) -> None:
        self.row = row
        self.params: tuple[str, ...] | None = None

    async def execute(self, sql: str, params: tuple[str, ...]) -> CursorFalso:
        self.params = params
        return CursorFalso(self.row)


class PoolFalso:
    def __init__(self, row: tuple[str, ...] | None) -> None:
        self.conexao = ConexaoFalsa(row)

    def connection(self) -> "PoolFalso":
        return self

    async def __aenter__(self) -> ConexaoFalsa:
        return self.conexao

    async def __aexit__(self, *args: object) -> None:
        return None


async def test_o_runner_resolve_slug_para_uuid() -> None:
    """`--tenant eval-local` continua sendo a interface humana; o banco passa a
    guardar uuid. Sem a resolução, o harness escreveria um tenant inexistente e a
    rodada mediria uma base vazia."""
    from sensus_rag.evals.runner import resolve_tenant

    pool = PoolFalso((UUID_DO_EVAL,))

    assert await resolve_tenant(pool, "eval-local") == UUID_DO_EVAL  # type: ignore[arg-type]
    assert pool.conexao.params == ("eval-local",)


async def test_slug_inexistente_e_erro_claro() -> None:
    """Abortar com mensagem é melhor que indexar 26 perguntas num tenant que não
    existe e descobrir no relatório vazio, 26 minutos depois."""
    from sensus_rag.evals.runner import TenantNotFound, resolve_tenant

    with pytest.raises(TenantNotFound):
        await resolve_tenant(PoolFalso(None), "nao-existe")  # type: ignore[arg-type]

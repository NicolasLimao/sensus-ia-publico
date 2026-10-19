"""Comando de medição: indexa o corpus, roda o dataset, imprime recall@k e MRR.

Fora do pytest de propósito — gasta chamadas de embedding e rede, então não pode estar
no caminho de cada commit.

Uso:
    uv run python -m sensus_rag.evals.runner --tenant eval-local
"""

import argparse
import asyncio
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import httpx
import yaml
from psycopg_pool import AsyncConnectionPool

from sensus_rag.chunking import StructuralChunker
from sensus_rag.config import RagSettings, connection_url, get_rag_settings
from sensus_rag.contract import (
    AnswerGenerator,
    AnswerRefusal,
    AnswerToken,
    GenerationConfig,
    Reranker,
    Retriever,
)
from sensus_rag.embedding.voyage import VoyageEmbedder
from sensus_rag.errors import GenerationError, IngestionError
from sensus_rag.evals.dataset import DatasetEntry, UnansweredEntry, load_dataset, load_unanswerable
from sensus_rag.evals.metrics import hit_at, mean_reciprocal_rank, rate, recall_at_k
from sensus_rag.generation.openai import OpenAIAnswerGenerator
from sensus_rag.ingestion import DocumentIngestor
from sensus_rag.rate_limit import limiter_from
from sensus_rag.repository.postgres import PostgresChunkRepository
from sensus_rag.reranking.voyage import VoyageReranker
from sensus_rag.retrieval import HybridRetriever
from sensus_rag.tenant_scope import TenantScope


class TenantNotFound(Exception):
    """Slug de tenant que não existe em `organizations`."""


async def resolve_tenant(pool: AsyncConnectionPool, slug: str) -> str:
    """Traduz o slug humano do `--tenant` para o uuid que o banco guarda.

    Desde a migração 003 o `tenant_id` é uuid com chave estrangeira. Sem esta tradução
    o harness escreveria num tenant inexistente — e o sintoma seria um relatório vazio
    ao fim de uma rodada inteira, não um erro no começo.
    """
    async with pool.connection() as connection:
        cursor = await connection.execute("select id from organizations where slug = %s", (slug,))
        row = await cursor.fetchone()
    if row is None:
        raise TenantNotFound(slug)
    return str(row[0])


ROOT = Path(__file__).resolve().parents[3]
CORPUS = ROOT / "evals" / "corpus"
DATASET = ROOT / "evals" / "dataset.yaml"
UNANSWERED = ROOT / "evals" / "dataset_sem_resposta.yaml"
OUT = ROOT / "evals" / "out"
KS = (1, 3, 5, 10)


async def run(
    tenant_id: str,
    settings: RagSettings,
    *,
    generation: bool = False,
    rerank: bool = False,
) -> int:
    try:
        entries = load_dataset(DATASET)
    except (ValueError, yaml.YAMLError, OSError) as exc:
        # Quem roda este comando costuma estar no meio de anotar o dataset à mão —
        # uma stack trace de YAML (sintaxe) ou de sistema de arquivos (arquivo
        # ausente) não ajuda; uma mensagem direta, apontando para o README, ajuda.
        print(
            f"dataset inválido em {DATASET}: {exc} — veja evals/README.md",
            file=sys.stderr,
        )
        return 1

    files = sorted(p for p in CORPUS.iterdir() if p.is_file() and p.name != ".gitkeep")
    if not entries or not files:
        print("corpus ou dataset vazios — veja evals/README.md", file=sys.stderr)
        return 1

    async with (
        httpx.AsyncClient(timeout=60.0) as http,
        AsyncConnectionPool(connection_url(settings), open=False) as pool,
    ):
        await pool.open()
        try:
            tenant_id = await resolve_tenant(pool, tenant_id)
        except TenantNotFound:
            print(
                f"tenant '{tenant_id}' não existe em organizations — "
                "crie a organização antes de medir",
                file=sys.stderr,
            )
            return 1
        # Embedder e reranker batem na MESMA conta Voyage: uma única instância de
        # limitador para os dois, senão cada um acredita ter a cota inteira (mesmo
        # padrão de `build_rag`).
        voyage_limiter = limiter_from(
            settings.embedding_requests_per_minute,
            settings.embedding_tokens_per_minute,
        )
        embedder = VoyageEmbedder(
            api_key=settings.voyage_api_key,
            model=settings.embedding_model,
            dimensions=settings.embedding_dimensions,
            client=http,
            limiter=voyage_limiter,
            max_batch_tokens=settings.embedding_max_batch_tokens,
        )
        reranker: Reranker | None = None
        if rerank:
            reranker = VoyageReranker(
                api_key=settings.voyage_api_key,
                model=settings.rerank_model,
                client=http,
                limiter=voyage_limiter,
                max_request_tokens=settings.rerank_max_request_tokens,
                max_document_chars=settings.rerank_max_document_chars,
            )
        repository = PostgresChunkRepository(
            scope=TenantScope(pool=pool), dimensions=settings.embedding_dimensions
        )
        ingestor = DocumentIngestor(
            chunker=StructuralChunker(
                target_chars=settings.chunk_target_chars,
                overlap_chars=settings.chunk_overlap_chars,
            ),
            embedder=embedder,
            repository=repository,
            max_bytes=settings.max_upload_bytes,
        )
        retriever = HybridRetriever(
            repository=repository,
            embedder=embedder,
            candidates=settings.retrieval_candidates,
            rrf_k=settings.rrf_k,
        )

        for file in files:
            try:
                result = await ingestor.ingest(
                    tenant_id=tenant_id,
                    name=file.name,
                    content_type=_content_type(file),
                    data=file.read_bytes(),
                )
            except IngestionError as exc:
                # Um corpus indexado pela metade produz uma medição que parece válida
                # e não é — o pior resultado possível para um harness que existe para
                # medir de verdade. Aborta em vez de seguir para o próximo arquivo.
                print(f"falha ao indexar {file.name}: {exc}", file=sys.stderr)
                return 1
            print(f"indexado: {file.name} ({result.chunk_count} chunks)")

        positions, reranked = await measure_retrieval(
            retriever,
            entries,
            tenant_id=tenant_id,
            reranker=reranker,
            candidates=settings.rerank_candidates,
        )

        if generation:
            generator = OpenAIAnswerGenerator(
                api_key=settings.openai_api_key,
                client=http,
                timeout_seconds=settings.generation_timeout_seconds,
                limiter=limiter_from(settings.generation_requests_per_minute, 0),
            )
            await measure_generation(
                retriever, generator, settings, tenant_id=tenant_id, answered=entries
            )

    _print_report(settings, entries_total=len(entries), positions=positions, reranked=reranked)
    return 0


async def measure_retrieval(
    retriever: Retriever,
    entries: Sequence[DatasetEntry],
    *,
    tenant_id: str,
    reranker: Reranker | None = None,
    candidates: int = 20,
) -> tuple[list[int | None], list[int | None] | None]:
    """Posições da lista fundida e, quando há reranker, da reordenada.

    UMA busca por pergunta alimenta as duas: mesmos candidatos, mesma rodada, mesmo
    minuto. Entre 2026-09-01 e 2026-09-02, sem ninguém tocar na recuperação, recall@1
    foi de 0,654 a 0,692 — um A/B entre rodadas mediria esse ruído junto com o efeito.

    A busca pede `max(max(KS), candidates)` resultados: o reranker precisa de mais
    candidatos do que as métricas leem. A coluna fundida só olha os primeiros
    `max(KS)` — sem essa fatia, com `candidates` maior que `max(KS)` na mão, um acerto
    fora da janela normal de leitura contaria (ex.: posição 15 como 1/15 em vez de
    None), e a coluna deixaria de ser comparável à linha de base do SDD, medida sobre
    `max(KS)` resultados.

    O reranker é usado DIRETO, sem o `RerankingRetriever`: aqui uma queda silenciosa
    para a ordem fundida produziria um A/B que parece válido e não é. Se o provedor
    falhar, a exceção sobe — mesma regra que já faz `run()` abortar quando o corpus
    indexa pela metade.
    """
    fused: list[int | None] = []
    reranked: list[int | None] | None = None if reranker is None else []

    for entry in entries:
        found = await retriever.search(
            tenant_id=tenant_id,
            question=entry.pergunta,
            top_k=max(max(KS), candidates),
        )
        fused.append(hit_at(entry, found[: max(KS)]))
        if reranker is not None and reranked is not None:
            ordered = await reranker.rerank(question=entry.pergunta, chunks=found, top_k=max(KS))
            if len(ordered) < min(max(KS), len(found)):
                # O corte de orçamento do provedor pode devolver menos candidatos do
                # que a janela de leitura: aí a coluna reordenada NÃO TEM como acertar
                # em @max(KS), e o A/B vira contra o rerank por configuração, não por
                # qualidade. Uma rodada de ~26 min não pode terminar com isso invisível.
                print(
                    f"aviso: rerank devolveu {len(ordered)} de {len(found)} candidatos "
                    f"para {entry.pergunta!r} — menos que a janela de leitura "
                    f"(max(KS)={max(KS)}); o delta desta pergunta subestima o rerank",
                    file=sys.stderr,
                )
            reranked.append(hit_at(entry, ordered))

    return fused, reranked


async def _answer(
    retriever: Retriever,
    generator: AnswerGenerator,
    *,
    tenant_id: str,
    question: str,
    top_k: int,
    config: GenerationConfig,
) -> tuple[str, bool]:
    """Devolve `(texto, recusou)`. É a mesma costura que o `RagUpstream` faz.

    Tipado com o Protocol `AnswerGenerator`, não com `OpenAIAnswerGenerator`: é o que
    permite testar esta função com `ScriptedGenerator`, sem rede.
    """
    found = await retriever.search(tenant_id=tenant_id, question=question, top_k=top_k)
    if not found:
        return "", True

    pieces: list[str] = []
    refused = False
    async for event in generator.generate(question=question, chunks=found, config=config):
        match event:
            case AnswerToken(text=text):
                pieces.append(text)
            case AnswerRefusal():
                refused = True
    return "".join(pieces), refused


async def measure_generation(
    retriever: Retriever,
    generator: AnswerGenerator,
    settings: RagSettings,
    *,
    tenant_id: str,
    answered: Sequence[DatasetEntry],
) -> None:
    """Roda as perguntas com e sem resposta, imprime as duas taxas e grava as respostas.

    Recebe `generator` e `answered` prontos em vez de construir/carregar sozinha:
    quem monta o gerador e quem carrega e valida o dataset é `run()`, que já faz as
    duas coisas para o passe de recuperação — reconstruir aqui seria fazer o trabalho
    duas vezes.

    Cada pergunta roda isolada num `try/except GenerationError`: se o provedor cair na
    pergunta 20 de 38, a rodada conta essa pergunta como falha e segue para a próxima
    — sem isso, a exceção sobe, mata o processo com traceback, e as respostas já pagas
    das perguntas anteriores se perdem junto.
    """
    unanswered = load_unanswerable(UNANSWERED)
    top_k = settings.generation_top_k
    # O harness mede com a config do AMBIENTE, nunca com a de um tenant: ele existe
    # para comparar mudanças NOSSAS (chunking, fusão, prompt) contra a linha de base de
    # 2026-09-02, e um briefing de cliente no meio tornaria as rodadas incomparáveis.
    config = GenerationConfig(
        model=settings.generation_model,
        max_context_chars=settings.generation_max_context_chars,
    )

    answers: list[tuple[str, str, bool]] = []
    failures: list[str] = []
    for entry in answered:
        try:
            text, refused = await _answer(
                retriever,
                generator,
                tenant_id=tenant_id,
                question=entry.pergunta,
                top_k=top_k,
                config=config,
            )
        except GenerationError as exc:
            failures.append(entry.pergunta)
            print(f"[com resposta] FALHOU ({exc}): {entry.pergunta}", file=sys.stderr)
            continue
        answers.append((entry.pergunta, text, refused))
        print(f"[com resposta] {'RECUSOU' if refused else 'respondeu'}: {entry.pergunta}")

    refusals: list[tuple[UnansweredEntry, bool]] = []
    for unanswered_entry in unanswered:
        try:
            _, refused = await _answer(
                retriever,
                generator,
                tenant_id=tenant_id,
                question=unanswered_entry.pergunta,
                top_k=top_k,
                config=config,
            )
        except GenerationError as exc:
            failures.append(unanswered_entry.pergunta)
            print(f"[sem resposta] FALHOU ({exc}): {unanswered_entry.pergunta}", file=sys.stderr)
            continue
        refusals.append((unanswered_entry, refused))
        print(
            f"[sem resposta] {'recusou' if refused else 'RESPONDEU'}: {unanswered_entry.pergunta}"
        )

    _print_generation_report(settings, answers=answers, refusals=refusals, failures=failures)
    _write_answers(answers)


def _print_generation_report(
    settings: RagSettings,
    *,
    answers: list[tuple[str, str, bool]],
    refusals: list[tuple[UnansweredEntry, bool]],
    failures: list[str],
) -> None:
    print("\n--- geração ---")
    print(f"modelo:      {settings.generation_model}")
    print(f"top_k:       {settings.generation_top_k}")

    if failures:
        print(f"\nfalhas do provedor ({len(failures)}) — fora das taxas abaixo:")
        for pergunta in failures:
            print(f"  - {pergunta}")

    print(
        f"\ntaxa_de_resposta ({len(answers)} com resposta): "
        f"{rate([not refused for _, _, refused in answers]):.3f}"
    )

    for tipo in sorted({entry.tipo for entry, _ in refusals}):
        da_familia = [refused for entry, refused in refusals if entry.tipo == tipo]
        print(f"taxa_de_recusa [{tipo}] ({len(da_familia)}): {rate(da_familia):.3f}")

    print(
        f"taxa_de_recusa [total] ({len(refusals)}): "
        f"{rate([refused for _, refused in refusals]):.3f}"
    )

    inventou = [entry.pergunta for entry, refused in refusals if not refused]
    if inventou:
        print("\nRESPONDEU sem ter base — ler estas com atenção:")
        for pergunta in inventou:
            print(f"  - {pergunta}")


def _write_answers(answers: list[tuple[str, str, bool]]) -> None:
    """Grava as respostas para a leitura humana única que vira linha de base qualitativa."""
    OUT.mkdir(parents=True, exist_ok=True)
    destination = OUT / f"respostas-{date.today().isoformat()}.md"
    blocks = [
        f"## {pergunta}\n\n{'**RECUSOU**' if refused else text.strip() or '(vazio)'}\n"
        for pergunta, text, refused in answers
    ]
    destination.write_text(
        "# Respostas fundamentadas — leitura de conferência\n\n" + "\n".join(blocks),
        encoding="utf-8",
    )
    print(f"\nrespostas gravadas em {destination}")


def _content_type(file: Path) -> str:
    return {
        ".pdf": "application/pdf",
        ".md": "text/markdown",
        ".txt": "text/plain",
    }.get(file.suffix.lower(), "application/octet-stream")


def _print_report(
    settings: RagSettings,
    *,
    entries_total: int,
    positions: list[int | None],
    reranked: list[int | None] | None = None,
) -> None:
    print("\n--- configuração ---")
    print(f"modelo:      {settings.embedding_model} ({settings.embedding_dimensions}d)")
    print(f"chunking:    alvo={settings.chunk_target_chars} overlap={settings.chunk_overlap_chars}")
    print(f"rrf_k:       {settings.rrf_k}  candidatos por perna: {settings.retrieval_candidates}")
    if reranked is not None:
        print(f"rerank:      {settings.rerank_model}  candidatos: {settings.rerank_candidates}")
    print(f"perguntas:   {entries_total}")

    print("\n--- resultado ---")
    if reranked is None:
        for k in KS:
            print(f"recall@{k}: {recall_at_k(positions, k=k):.3f}")
        print(f"MRR:       {mean_reciprocal_rank(positions):.3f}")
    else:
        print(f"{'':<12}{'fundido':>10}{'rerank':>10}{'delta':>10}")
        for k in KS:
            base, novo = recall_at_k(positions, k=k), recall_at_k(reranked, k=k)
            print(f"recall@{k:<6}{base:>10.3f}{novo:>10.3f}{novo - base:>+10.3f}")
        base, novo = mean_reciprocal_rank(positions), mean_reciprocal_rank(reranked)
        print(f"{'MRR':<12}{base:>10.3f}{novo:>10.3f}{novo - base:>+10.3f}")

    misses = [i for i, p in enumerate(positions, start=1) if p is None]
    if misses:
        print(f"\nsem acerto nas perguntas: {misses}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Mede a recuperação do Sensus.")
    parser.add_argument("--tenant", default="eval-local", help="tenant usado na medição")
    parser.add_argument(
        "--generation",
        action="store_true",
        help="também mede a geração (gasta chamadas do provedor de geração)",
    )
    parser.add_argument(
        "--rerank",
        action="store_true",
        help="mede também a lista reordenada (gasta chamadas de rerank da mesma cota Voyage)",
    )
    args = parser.parse_args()

    settings = get_rag_settings()
    if not settings.database_url or not settings.voyage_api_key:
        print("SENSUS_DATABASE_URL e SENSUS_VOYAGE_API_KEY são obrigatórias.", file=sys.stderr)
        return 1

    if args.generation and not (settings.openai_api_key and settings.generation_model):
        print(
            "--generation exige SENSUS_OPENAI_API_KEY e SENSUS_GENERATION_MODEL.",
            file=sys.stderr,
        )
        return 1

    if args.rerank and args.generation:
        print(
            "--rerank e --generation não rodam juntos: seriam ~38 chamadas de rerank "
            "a ~1/min sobre os ~36 min da geração. Rode a recuperação primeiro.",
            file=sys.stderr,
        )
        return 1

    if args.rerank and not settings.rerank_model:
        print("--rerank exige SENSUS_RERANK_MODEL.", file=sys.stderr)
        return 1

    # No Windows, o loop padrão do asyncio (ProactorEventLoop) não roda o psycopg em
    # modo assíncrono — a conexão ao Postgres falha antes de tentar autenticar (mesmo
    # problema documentado em tests/conftest.py para a suíte de integração). Como este
    # comando roda fora do pytest, precisa da própria proteção antes do asyncio.run.
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    return asyncio.run(run(args.tenant, settings, generation=args.generation, rerank=args.rerank))


if __name__ == "__main__":
    raise SystemExit(main())

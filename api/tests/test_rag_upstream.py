"""O adaptador costura busca e geração e traduz para o contrato SSE da fatia 1."""

from collections.abc import Sequence

import pytest

from sensus_api.ai_config_resolver import AiConfigResolver
from sensus_api.ai_configs import AiConfig
from sensus_api.ai_configs_memory import InMemoryAiConfigDirectory
from sensus_api.upstream.contract import (
    Chunk,
    ResultEvent,
    StageEvent,
    TokenEvent,
    UpstreamTimeout,
    UpstreamUnavailable,
)
from sensus_api.upstream.rag_upstream import REFUSAL_TEXT, RagUpstream
from sensus_api.users_memory import InMemoryUserDirectory
from sensus_rag.contract import AnswerRefusal, AnswerToken, RetrievedChunk
from sensus_rag.errors import (
    EmbeddingUnavailable,
    GenerationTimeout,
    GenerationUnavailable,
    RepositoryUnavailable,
)
from sensus_rag.generation.fake import ScriptedGenerator
from sensus_rag.generation.prompt import build_system

FOUND = [
    RetrievedChunk(
        chunk_id="c1",
        document_id="d1",
        document_name="cdc.md",
        content="o prazo é de sete dias",
        score=0.4,
    ),
    RetrievedChunk(
        chunk_id="c2",
        document_id="d2",
        document_name="lgpd.md",
        content="outro trecho",
        score=0.2,
    ),
]


class StubRetriever:
    def __init__(self, results: Sequence[RetrievedChunk], error: Exception | None = None) -> None:
        self.results = list(results)
        self.error = error
        self.calls: list[tuple[str, str, int]] = []

    async def search(self, *, tenant_id: str, question: str, top_k: int) -> list[RetrievedChunk]:
        self.calls.append((tenant_id, question, top_k))
        if self.error is not None:
            raise self.error
        return self.results


async def ask(upstream: RagUpstream) -> list[object]:
    return [
        event
        async for event in upstream.ask(question="Qual o prazo?", user_id="u1", tenant_id="t1")
    ]


async def test_ordem_dos_eventos_e_estagio_de_rerank_ausente() -> None:
    upstream = RagUpstream(
        retriever=StubRetriever(FOUND),
        generator=ScriptedGenerator([AnswerToken("Sete "), AnswerToken("dias.")]),
        configs=_resolver(),
    )

    events = await ask(upstream)

    assert events[0] == StageEvent("retrieving")
    assert events[1] == StageEvent("generating")
    assert events[2:4] == [TokenEvent("Sete "), TokenEvent("dias.")]
    assert isinstance(events[-1], ResultEvent)
    assert StageEvent("reranking") not in events


async def test_resultado_traz_o_nome_do_documento_e_a_media_do_score() -> None:
    upstream = RagUpstream(
        retriever=StubRetriever(FOUND),
        generator=ScriptedGenerator([AnswerToken("ok")]),
        configs=_resolver(),
    )

    result = (await ask(upstream))[-1]

    assert isinstance(result, ResultEvent)
    assert result.chunks == (
        Chunk(id="c1", excerpt="o prazo é de sete dias", score=0.4, document="cdc.md"),
        Chunk(id="c2", excerpt="outro trecho", score=0.2, document="lgpd.md"),
    )
    assert result.score == pytest.approx(0.3)


async def test_a_busca_alimenta_a_geracao_com_o_top_k_configurado() -> None:
    retriever = StubRetriever(FOUND)
    generator = ScriptedGenerator([AnswerToken("ok")])

    await ask(
        RagUpstream(
            retriever=retriever,
            generator=generator,
            configs=_resolver(generation_top_k=3),
        )
    )

    assert retriever.calls == [("t1", "Qual o prazo?", 3)]
    pergunta, quantos, _ = generator.calls[0]
    assert (pergunta, quantos) == ("Qual o prazo?", 2)


async def test_recusa_emite_a_frase_fixa_e_zera_as_fontes() -> None:
    upstream = RagUpstream(
        retriever=StubRetriever(FOUND),
        generator=ScriptedGenerator([AnswerRefusal()]),
        configs=_resolver(),
    )

    events = await ask(upstream)

    assert TokenEvent(REFUSAL_TEXT) in events
    assert events[-1] == ResultEvent(chunks=(), score=None)


async def test_base_vazia_recusa_sem_gastar_chamada_de_geracao() -> None:
    generator = ScriptedGenerator([AnswerToken("nunca deveria sair")])
    upstream = RagUpstream(retriever=StubRetriever([]), generator=generator, configs=_resolver())

    events = await ask(upstream)

    assert generator.calls == []
    assert events[-1] == ResultEvent(chunks=(), score=None)


@pytest.mark.parametrize(
    "error", [RepositoryUnavailable("banco caiu"), EmbeddingUnavailable("embedding caiu")]
)
async def test_falha_da_busca_seja_repositorio_ou_embedding_vira_service_unavailable(
    error: Exception,
) -> None:
    upstream = RagUpstream(
        retriever=StubRetriever(FOUND, error=error),
        generator=ScriptedGenerator([]),
        configs=_resolver(),
    )

    with pytest.raises(UpstreamUnavailable):
        await ask(upstream)


async def test_timeout_da_geracao_vira_upstream_timeout() -> None:
    upstream = RagUpstream(
        retriever=StubRetriever(FOUND),
        generator=ScriptedGenerator([], error=GenerationTimeout("demorou")),
        configs=_resolver(),
    )

    with pytest.raises(UpstreamTimeout):
        await ask(upstream)


async def test_falha_da_geracao_vira_service_unavailable() -> None:
    upstream = RagUpstream(
        retriever=StubRetriever(FOUND),
        generator=ScriptedGenerator([], error=GenerationUnavailable("provedor caiu")),
        configs=_resolver(),
    )

    with pytest.raises(UpstreamUnavailable):
        await ask(upstream)


async def test_excecao_de_geracao_apos_tokens_emitidos_ainda_vira_service_unavailable() -> None:
    """Tokens já emitidos antes da falha não podem se perder: o navegador já os
    recebeu, e a exceção que sobe é sobre o que faltou, não sobre o que já saiu."""
    upstream = RagUpstream(
        retriever=StubRetriever(FOUND),
        generator=ScriptedGenerator(
            [AnswerToken("parcial")], error=GenerationUnavailable("caiu no meio")
        ),
        configs=_resolver(),
    )

    events: list[object] = []
    with pytest.raises(UpstreamUnavailable):
        async for event in upstream.ask(question="Qual o prazo?", user_id="u1", tenant_id="t1"):
            events.append(event)

    assert TokenEvent("parcial") in events


async def test_teto_de_contexto_cita_so_os_trechos_que_entraram_no_prompt() -> None:
    """I1: `RagUpstream` não pode citar um trecho que `fit_chunks` já descartou do
    prompt — senão a UI atribui a resposta a um documento que o modelo nunca viu."""
    budget = len(FOUND[0].content)
    upstream = RagUpstream(
        retriever=StubRetriever(FOUND),
        generator=ScriptedGenerator([AnswerToken("ok")]),
        configs=_resolver(generation_top_k=2, max_context_chars=budget),
    )

    result = (await ask(upstream))[-1]

    assert isinstance(result, ResultEvent)
    assert len(result.chunks) == 1
    assert result.chunks[0].id == "c1"


# --- A config do tenant governa a requisição (fatia 4a) ------------------------------


def _resolver(**campos: object) -> AiConfigResolver:
    """Resolvedor REAL sobre diretório em memória — não um duplo de resolvedor.

    A lição da 3e: um duplo no lugar do componente real esconde justamente o defeito
    que este teste existe para pegar, que é a config ser resolvida e depois ignorada.
    """
    base = {
        "briefing": "",
        "generation_model": "gpt-4o-mini",
        "generation_top_k": 5,
        "max_context_chars": 12_000,
        "suggestions": (),
    }
    base.update(campos)
    return AiConfigResolver(
        directory=InMemoryAiConfigDirectory(users=InMemoryUserDirectory()),
        defaults=AiConfig(**base),  # type: ignore[arg-type]
    )


async def test_o_top_k_da_config_e_o_que_a_busca_usa() -> None:
    """O modo de falhar silencioso desta fatia é a config ser resolvida e ignorada. O
    `top_k` é o campo onde isso não daria erro nenhum — só respostas piores."""
    retriever = StubRetriever(FOUND)
    upstream = RagUpstream(
        retriever=retriever,
        generator=ScriptedGenerator([AnswerToken("ok")]),
        configs=_resolver(generation_top_k=9),
    )

    await ask(upstream)

    assert retriever.calls == [("t1", "Qual o prazo?", 9)]


async def test_o_briefing_da_config_chega_ao_gerador() -> None:
    generator = ScriptedGenerator([AnswerToken("ok")])
    upstream = RagUpstream(
        retriever=StubRetriever(FOUND),
        generator=generator,
        configs=_resolver(briefing="Somos a Acme.", generation_model="modelo-x"),
    )

    await ask(upstream)

    _, _, config = generator.calls[0]
    assert config.briefing == "Somos a Acme."
    assert config.model == "modelo-x"


async def test_o_teto_de_contexto_da_config_governa_as_FONTES_citadas() -> None:
    """O teto precisa ser o mesmo dos dois lados. Citar `found` inteiro quando o
    gerador viu menos atribuiria a resposta a um trecho que o modelo nunca leu."""
    longo = RetrievedChunk(
        chunk_id="c1",
        document_id="d1",
        document_name="cdc.md",
        content="x" * 50,
        score=0.9,
    )
    outro = RetrievedChunk(
        chunk_id="c2",
        document_id="d2",
        document_name="lgpd.md",
        content="y" * 50,
        score=0.2,
    )
    upstream = RagUpstream(
        retriever=StubRetriever([longo, outro]),
        generator=ScriptedGenerator([AnswerToken("ok")]),
        configs=_resolver(max_context_chars=60),
    )

    result = (await ask(upstream))[-1]

    assert isinstance(result, ResultEvent)
    assert [c.id for c in result.chunks] == ["c1"]


async def test_dois_tenants_com_configs_DIFERENTES_recebem_prompts_diferentes() -> None:
    """A costura inteira, de ponta a ponta: versão gravada → resolvedor → adaptador →
    prompt do gerador.

    Cada pedaço já tem teste próprio, e é exatamente por isso que este existe: três
    peças certas ligadas na ordem errada dariam a MESMA resposta aos dois tenants, sem
    nenhum teste de peça reclamar. É o defeito que a fatia 4a só pode ter uma vez.
    """
    directory = InMemoryAiConfigDirectory(users=InMemoryUserDirectory())
    resolver = AiConfigResolver(
        directory=directory,
        defaults=AiConfig(
            briefing="",
            generation_model="gpt-4o-mini",
            generation_top_k=5,
            max_context_chars=12_000,
            suggestions=(),
        ),
    )
    await directory.create_version(
        tenant_id="acme",
        created_by="u1",
        config=AiConfig(
            briefing="Somos a Acme.",
            generation_model="modelo-acme",
            generation_top_k=3,
            max_context_chars=8_000,
            suggestions=(),
        ),
    )
    await directory.create_version(
        tenant_id="globex",
        created_by="u2",
        config=AiConfig(
            briefing="Somos a Globex.",
            generation_model="modelo-globex",
            generation_top_k=7,
            max_context_chars=9_000,
            suggestions=(),
        ),
    )
    generator = ScriptedGenerator([AnswerToken("ok")])
    upstream = RagUpstream(retriever=StubRetriever(FOUND), generator=generator, configs=resolver)

    for tenant in ("acme", "globex"):
        async for _ in upstream.ask(question="Qual o prazo?", user_id="u", tenant_id=tenant):
            pass

    da_acme, da_globex = (chamada[2] for chamada in generator.calls)
    assert (da_acme.briefing, da_acme.model) == ("Somos a Acme.", "modelo-acme")
    assert (da_globex.briefing, da_globex.model) == ("Somos a Globex.", "modelo-globex")
    # O prompt em si, e não só o config: é o que o modelo de fato lê.
    assert "Somos a Acme." in build_system(da_acme.briefing)
    assert "Somos a Acme." not in build_system(da_globex.briefing)

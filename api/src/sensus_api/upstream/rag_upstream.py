"""Upstream real: recupera com a busca híbrida e gera com o LLM.

Substitui o `DemoUpstream` quando a geração está configurada. É aqui — e só aqui — que
os eventos de domínio de `sensus_rag` viram o contrato SSE da fatia 1, e é aqui que mora
o texto que o usuário lê: `sensus_rag` não carrega copy de interface.

O upstream é EM PROCESSO. A fatia 1 supôs que seria um serviço HTTP remoto; a 2b decide
que não é, e a chamada de rede que sobra é a do provedor de geração.
"""

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass

from sensus_api.ai_config_resolver import AiConfigResolver
from sensus_api.upstream.contract import (
    Chunk,
    ResultEvent,
    StageEvent,
    TokenEvent,
    UpstreamEvent,
    UpstreamTimeout,
    UpstreamUnavailable,
)
from sensus_rag.contract import (
    AnswerGenerator,
    AnswerRefusal,
    AnswerToken,
    RetrievedChunk,
    Retriever,
)
from sensus_rag.errors import (
    EmbeddingUnavailable,
    GenerationTimeout,
    GenerationUnavailable,
    RepositoryUnavailable,
)
from sensus_rag.generation.prompt import fit_chunks

# Copy do produto, não do pipeline — e continua fixa depois da fatia 4a. O que a config
# do tenant governa é COMO o modelo responde, não as frases da interface; a 4a registra
# isso como limitação deliberada.
REFUSAL_TEXT = (
    "Não encontrei essa informação nos documentos da base. "
    "Se ela deveria estar aí, verifique se o documento foi enviado."
)


@dataclass(frozen=True)
class RagUpstream:
    retriever: Retriever
    generator: AnswerGenerator
    # Desde a fatia 4a, `top_k` e teto de contexto NÃO são mais atributos de processo:
    # eles variam por tenant, e chegam resolvidos no começo de cada requisição. Deixar
    # cópias aqui daria duas verdades — e a errada seria a que o pipeline usa.
    configs: AiConfigResolver

    async def ask(
        self, *, question: str, user_id: str, tenant_id: str
    ) -> AsyncIterator[UpstreamEvent]:
        # Primeira coisa da requisição: tudo o que vem depois depende dela.
        config = await self.configs.resolve(tenant_id)

        yield StageEvent("retrieving")
        try:
            found = await self.retriever.search(
                tenant_id=tenant_id, question=question, top_k=config.generation_top_k
            )
        except (EmbeddingUnavailable, RepositoryUnavailable) as exc:
            raise UpstreamUnavailable("recuperação indisponível") from exc

        # Base vazia não é limiar de score: é ausência de qualquer trecho. Chamar o
        # provedor para ele concluir o óbvio custaria dinheiro e latência.
        if not found:
            yield StageEvent("generating")
            yield TokenEvent(REFUSAL_TEXT)
            yield ResultEvent(chunks=(), score=None)
            return

        yield StageEvent("generating")
        refused = False
        try:
            async for event in self.generator.generate(
                question=question, chunks=found, config=config.for_generation()
            ):
                match event:
                    case AnswerToken(text=text):
                        yield TokenEvent(text)
                    case AnswerRefusal():
                        refused = True
        except GenerationTimeout as exc:
            raise UpstreamTimeout("geração não respondeu no prazo") from exc
        except GenerationUnavailable as exc:
            raise UpstreamUnavailable("geração indisponível") from exc

        if refused:
            # Sem fundamento não é erro: é o sistema acertando ao recusar. Vai como
            # `done` com `chunks_used: 0`, decisão da fatia 1.
            yield TokenEvent(REFUSAL_TEXT)
            yield ResultEvent(chunks=(), score=None)
            return

        # O MESMO teto que o gerador usou para montar o prompt — por isso ele sai
        # da config resolvida, e não de um atributo com cópia. Citar `found`
        # inteiro atribuiria a resposta a um trecho que o modelo nunca viu.
        cited = _cited(fit_chunks(found, config.max_context_chars))
        yield ResultEvent(chunks=cited, score=_mean_score(cited))


def _cited(found: Sequence[RetrievedChunk]) -> tuple[Chunk, ...]:
    """`document` deixa de nascer `None`: a recuperação tem o nome do documento."""
    return tuple(
        Chunk(
            id=chunk.chunk_id,
            excerpt=chunk.content,
            score=chunk.score,
            document=chunk.document_name,
        )
        for chunk in found
    )


def _mean_score(chunks: Sequence[Chunk]) -> float | None:
    """Média do score de RRF dos trechos citados.

    Não é confiança e não vai para a tela como tal — decisão da fatia 1. Serve de
    diagnóstico no `done` e no log.
    """
    if not chunks:
        return None
    return sum(chunk.score for chunk in chunks) / len(chunks)

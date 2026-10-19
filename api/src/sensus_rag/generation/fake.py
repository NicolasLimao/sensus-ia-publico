"""Duplo da geração: emite um roteiro fixo e, opcionalmente, estoura.

Espelha o `ScriptedUpstream` do adaptador pelo mesmo motivo: um único duplo cobre o
caso que responde e o que falha, porque falhar é só um roteiro que termina em exceção.
"""

from collections.abc import AsyncIterator, Sequence

from sensus_rag.contract import GenerationConfig, GenerationEvent, RetrievedChunk
from sensus_rag.errors import GenerationError

# Pergunta, quantos trechos e o config da requisição. O terceiro entrou na fatia
# 4a: é como o teste do adaptador prova que a config do tenant chegou ao gerador,
# e não ficou resolvida e esquecida no caminho.
GenerateCall = tuple[str, int, GenerationConfig]

# O config que os testes passam quando o que se mede não é a config em si.
CONFIG_DE_DUPLO = GenerationConfig(model="duplo")


class ScriptedGenerator:
    def __init__(
        self, events: Sequence[GenerationEvent], *, error: GenerationError | None = None
    ) -> None:
        self.events = list(events)
        self.error = error
        # Pergunta e quantos trechos chegaram — o suficiente para o teste do
        # adaptador provar que a busca alimentou a geração.
        self.calls: list[GenerateCall] = []

    async def generate(
        self,
        *,
        question: str,
        chunks: Sequence[RetrievedChunk],
        # Sem default: o `Protocol` e o gerador real exigem o config, e um default
        # aqui só serviria para deixar passar o teste que esquecesse de passá-lo —
        # a divergência duplo/real em miniatura.
        config: GenerationConfig,
    ) -> AsyncIterator[GenerationEvent]:
        self.calls.append((question, len(chunks), config))
        for event in self.events:
            yield event
        if self.error is not None:
            raise self.error

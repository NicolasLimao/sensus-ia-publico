"""Contrato da configuração de IA por tenant.

`sensus_api` conhece tenant; `sensus_rag` não — ele é o RAG genérico, e o que atravessa
a fronteira é o `GenerationConfig`, sem noção de organização nenhuma. Esta separação é a
mesma que `users.py` documenta, e é ela que mantém o pacote de RAG reaproveitável.

Versões são **imutáveis**: salvar cria uma nova, e rollback é ativar uma anterior. Não
há `update` de conteúdo em lugar nenhum desta porta, e é isso que dá trilha de auditoria
de graça.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sensus_rag.contract import GenerationConfig


class ConcurrentConfigWrite(Exception):
    """Outra gravação do mesmo tenant chegou primeiro, e esta perdeu a corrida.

    Não é erro do servidor: o banco protegeu o dado como devia — `unique (tenant_id,
    version)` — e o que sobra é contar isso a quem salvou. Perder uma gravação
    concorrente de config é melhor que gravar duas versões com o mesmo número.
    """


@dataclass(frozen=True)
class AiConfig:
    """A configuração **resolvida** de um tenant: nunca parcial.

    Quem tem linha ativa recebe os valores dela; quem não tem recebe os do ambiente. A
    borda e o pipeline lidam com uma forma só, e nenhum deles precisa de um ramo
    "ainda não configurou".
    """

    briefing: str
    generation_model: str
    generation_top_k: int
    max_context_chars: int
    suggestions: tuple[str, ...]

    def for_generation(self) -> GenerationConfig:
        """Recorta o que o `sensus_rag` entende.

        O `generation_top_k` NÃO entra: ele governa a busca, que acontece antes da
        geração e é decisão do adaptador. Mandá-lo junto convidaria o gerador a
        reinterpretá-lo.
        """
        return GenerationConfig(
            model=self.generation_model,
            briefing=self.briefing,
            max_context_chars=self.max_context_chars,
        )


@dataclass(frozen=True)
class AiConfigVersion:
    """Uma versão gravada, com o que a tela de histórico precisa mostrar."""

    id: str
    version: int
    is_active: bool
    created_at: datetime
    created_by_email: str | None
    config: AiConfig


@dataclass(frozen=True)
class AiConfigVersionPage:
    """Uma página do histórico, e se existe versão depois dela.

    `has_more` sai de ler **uma linha a mais** que o limite, e não de um `count(*)`: a
    tela só precisa saber se o botão "carregar mais" tem o que carregar, e um total
    exato custaria uma varredura por abertura do painel para exibir um número que
    ninguém pediu.
    """

    versions: list[AiConfigVersion]
    has_more: bool


class AiConfigDirectory(Protocol):
    async def active_for(self, tenant_id: str) -> AiConfig | None:
        """A config ativa, ou `None` quando o tenant nunca salvou nada.

        `None` é deliberado, em vez de já devolver os defaults: "qual é o default" é
        decisão de produto, e o adaptador do banco não deveria precisar conhecê-la —
        quem a aplica é o `AiConfigResolver`.
        """
        ...

    async def list_versions(
        self, tenant_id: str, *, limit: int, offset: int = 0
    ) -> AiConfigVersionPage:
        """Da mais nova para a mais antiga, uma página por vez.

        `limit` é obrigatório de propósito: o histórico não tem teto — é uma linha por
        edição, e podar seria o contrário do que a fatia entrega —, e cada versão
        carrega o config inteiro. Um default aqui devolveria a coleção completa para
        quem não pensou no assunto, que é exatamente o caminho que ninguém percorre de
        olho aberto.
        """
        ...

    async def create_version(
        self, *, tenant_id: str, created_by: str, config: AiConfig
    ) -> AiConfigVersion:
        """Cria a versão seguinte e **a ativa**, num átomo só.

        Salvar publica: não existe rascunho. A tela avisa isso antes de salvar, porque
        "salvar" que publica e "salvar" que guarda são coisas diferentes, e confundir
        as duas vira suporte.

        Levanta `ConcurrentConfigWrite` quando outra gravação do mesmo tenant ganhou a
        corrida pelo número da versão. O duplo em memória **nunca** levanta — ele roda
        numa tarefa só e não suspende no meio daqui, então as duas gravações não se
        cruzam —, e é o par no Postgres que a produz. É a divergência conhecida desta
        porta: quem exercita o caminho do erro na borda é um diretório de teste que o
        levanta de propósito.
        """
        ...

    async def activate(self, *, tenant_id: str, config_id: str) -> bool:
        """Rollback. `False` quando nada foi afetado — id inexistente **ou de outro
        tenant**, que a borda não distingue.

        Levanta `ConcurrentConfigWrite` quando outra troca do mesmo tenant comitou no
        meio desta: o índice de uma ativa por tenant recusa a segunda. Como em
        `create_version`, só o par no Postgres produz esse erro.
        """
        ...

"""Diretório de config de IA no Postgres.

Só `TenantScope`, sem `pool` cru — o oposto do convite e do reset, e é a diferença que
a fatia 4a registra: **todo** caminho que lê ou escreve config tem tenant (o admin
editando, o chat resolvendo). Não há convidado nem anônimo, então não há fresta
`security definer` nenhuma.

Consequência deliberada, a mesma que a 3c registrou: os comandos abaixo **não** têm
`where tenant_id = %s`. Quem isola é a policy da migração 010, e um `where` por cima
esconderia uma policy quebrada — o teste de leitura cruzada ficaria passando por engano.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import psycopg

from sensus_api.ai_configs import (
    AiConfig,
    AiConfigVersion,
    AiConfigVersionPage,
    ConcurrentConfigWrite,
)
from sensus_rag.tenant_scope import TenantScope

# A ordem é o contrato entre as três consultas abaixo e `_version_from_row`. Copiada em
# cada uma, seriam três lugares onde o índice posicional pode divergir em silêncio —
# e nenhum tipo pega isso, a mesma razão que criou o `account_from_row`.
_COLUNAS = """
    c.id, c.version, c.is_active, c.created_at, u.email,
    c.briefing, c.generation_model, c.generation_top_k, c.max_context_chars,
    c.suggestions
"""

_DE = "from ai_configs c left join users u on u.id = c.created_by"


def _is_uuid(value: str) -> bool:
    """O id vem da URL, então pode ser qualquer texto.

    Sem esta guarda, `where id = 'nao-existe'` não é "não achei": o Postgres levanta
    `invalid input syntax for type uuid` e a rota vira 500, enquanto o duplo em memória
    devolve `False` e a borda responde 404. É a divergência duplo/real que a 2a pagou
    caro — `sensus_rag/repository/postgres.py` carrega a mesma guarda pelo mesmo motivo.
    """
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


@contextmanager
def _corrida_vira_erro_de_dominio() -> Iterator[None]:
    """Traduz as duas corridas que os índices desta tabela recusam.

    São duas, e as duas chegam como `23505`: `unique (tenant_id, version)`, quando dois
    admins salvam no mesmo instante, e `ai_configs_uma_ativa_por_tenant`, quando dois
    rollbacks se cruzam — a segunda transação não enxerga a linha que a primeira acabou
    de ativar, ativa a sua por cima, e o índice parcial recusa.

    Solta, a `UniqueViolation` sobe crua até a borda e vira 500: "o servidor quebrou",
    para um caso em que o banco protegeu o dado exatamente como devia. A tradução mora
    aqui porque é aqui que se sabe qual índice falhou — a borda só veria `23505`.
    """
    try:
        yield
    except psycopg.errors.UniqueViolation as violacao:
        raise ConcurrentConfigWrite from violacao


def _version_from_row(row: Any) -> AiConfigVersion:
    return AiConfigVersion(
        id=str(row[0]),
        version=row[1],
        is_active=row[2],
        created_at=row[3],
        created_by_email=row[4],
        config=AiConfig(
            briefing=row[5],
            generation_model=row[6],
            generation_top_k=row[7],
            max_context_chars=row[8],
            suggestions=tuple(row[9]),
        ),
    )


@dataclass(frozen=True)
class PostgresAiConfigDirectory:
    scope: TenantScope

    async def active_for(self, tenant_id: str) -> AiConfig | None:
        async with self.scope.connection(tenant_id) as connection:
            cursor = await connection.execute(f"select {_COLUNAS} {_DE} where c.is_active")
            row = await cursor.fetchone()
        return None if row is None else _version_from_row(row).config

    async def list_versions(
        self, tenant_id: str, *, limit: int, offset: int = 0
    ) -> AiConfigVersionPage:
        """Uma página, recortada pelo BANCO.

        Lê `limit + 1` linhas: a excedente não vai para a resposta e serve só para dizer
        se existe página seguinte. Um `count(*)` daria o total exato e custaria uma
        varredura por abertura do painel para exibir um número que ninguém pediu.
        """
        async with self.scope.connection(tenant_id) as connection:
            cursor = await connection.execute(
                f"select {_COLUNAS} {_DE} order by c.version desc limit %s offset %s",
                (limit + 1, offset),
            )
            rows = await cursor.fetchall()
        return AiConfigVersionPage(
            versions=[_version_from_row(row) for row in rows[:limit]],
            has_more=len(rows) > limit,
        )

    async def create_version(
        self, *, tenant_id: str, created_by: str, config: AiConfig
    ) -> AiConfigVersion:
        """Cria a próxima versão e a ativa, na MESMA transação que o `TenantScope` abre.

        O número da versão sai de um `max(version) + 1` lido dentro dela. Duas
        gravações simultâneas do mesmo tenant não geram número repetido: o
        `unique (tenant_id, version)` da migração recusa a segunda, que é o
        comportamento certo — perder uma gravação concorrente de config é melhor que
        gravar duas versões com o mesmo número.

        A perdedora sai daqui como `ConcurrentConfigWrite`. O escudo cobre a transação
        inteira, e não só o insert: a ativação que vem depois dele disputa o OUTRO
        índice — o de uma ativa por tenant — com qualquer rollback acontecendo no mesmo
        instante.
        """
        with _corrida_vira_erro_de_dominio():
            async with self.scope.connection(tenant_id) as connection:
                cursor = await connection.execute(
                    """
                    insert into ai_configs (
                        tenant_id, version, briefing, generation_model,
                        generation_top_k, max_context_chars, suggestions, created_by
                    )
                    select
                        %s,
                        coalesce(max(version), 0) + 1,
                        %s, %s, %s, %s, %s, %s
                    from ai_configs
                    returning id
                    """,
                    (
                        tenant_id,
                        config.briefing,
                        config.generation_model,
                        config.generation_top_k,
                        config.max_context_chars,
                        list(config.suggestions),
                        created_by,
                    ),
                )
                row = await cursor.fetchone()
                assert row is not None  # `insert … returning` devolve linha ou levanta
                criada_id = str(row[0])

                await self._ativar(connection, criada_id)

                cursor = await connection.execute(
                    f"select {_COLUNAS} {_DE} where c.id = %s", (criada_id,)
                )
                gravada = await cursor.fetchone()
                assert gravada is not None  # acabou de ser inserida nesta transação
        return _version_from_row(gravada)

    async def activate(self, *, tenant_id: str, config_id: str) -> bool:
        """Rollback.

        Levanta `ConcurrentConfigWrite` quando outro rollback do mesmo tenant comitou no
        meio deste: nenhum dos dois é o "certo", e trocar a versão ativa por cima de uma
        troca que a pessoa não viu é pior do que pedir para recarregar.
        """
        if not _is_uuid(config_id):
            return False
        with _corrida_vira_erro_de_dominio():
            async with self.scope.connection(tenant_id) as connection:
                # Confere a existência ANTES de trocar: sem isto, ativar um id
                # inexistente desativaria todas as versões do tenant e o deixaria sem
                # nenhuma ativa — uma chamada errada derrubando a config de quem não fez
                # nada.
                cursor = await connection.execute(
                    "select 1 from ai_configs where id = %s", (config_id,)
                )
                if await cursor.fetchone() is None:
                    # Inexistente ou de outro tenant: a policy não distingue os dois, e
                    # a borda também não — 404 nos dois casos.
                    return False
                await self._ativar(connection, config_id)
        return True

    @staticmethod
    async def _ativar(connection: Any, config_id: str) -> None:
        """A troca em DOIS comandos: desativa as ativas, depois ativa esta.

        O comando único (`set is_active = (id = %s)`) é o que parece certo e **não
        funciona** — medido no Postgres real em 2026-09-20, em transação revertida: o
        índice único parcial é verificado linha a linha, e se a linha a ativar for
        processada antes da que está ativa, as duas ficam `true` por um instante e o
        `update` estoura com `UniqueViolation`. A ordem das linhas não é nossa, então o
        comando único falharia de forma intermitente, dependendo do plano.

        O instante sem nenhuma ativa que os dois comandos criam é invisível de fora:
        está dentro da transação que o `TenantScope` abre, e sob MVCC as outras
        conexões continuam vendo o estado anterior até o commit.

        Nenhum dos dois comandos leva `where tenant_id`: quem restringe é a policy.
        """
        await connection.execute("update ai_configs set is_active = false where is_active")
        await connection.execute(
            "update ai_configs set is_active = true where id = %s", (config_id,)
        )

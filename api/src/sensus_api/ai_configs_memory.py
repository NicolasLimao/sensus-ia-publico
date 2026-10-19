"""Diretório de config de IA em memória — testes de rota e pipeline sem banco."""

import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from sensus_api.ai_configs import AiConfig, AiConfigVersion, AiConfigVersionPage
from sensus_api.users_memory import InMemoryUserDirectory


@dataclass
class InMemoryAiConfigDirectory:
    """O par do `PostgresAiConfigDirectory`.

    Duas propriedades que ele tem por construção e o par real tem por banco — "uma
    ativa por tenant" sob concorrência (índice único parcial) e o isolamento pela
    policy — NÃO são provadas aqui. É por isso que os testes de integração existem.

    `users` entra para resolver o e-mail de quem criou cada versão: no par real isso é
    um `join`, e sem ele o duplo não teria o que mostrar na tela de histórico.
    """

    users: InMemoryUserDirectory
    versions: list[AiConfigVersion] = field(default_factory=list)
    # id da versão -> tenant. No par real isto é a coluna `tenant_id`, e quem filtra
    # por ela é a policy; aqui precisa ser explícito.
    _tenants: dict[str, str] = field(default_factory=dict)

    def _do_tenant(self, tenant_id: str) -> list[AiConfigVersion]:
        return [v for v in self.versions if self._tenants[v.id] == tenant_id]

    async def active_for(self, tenant_id: str) -> AiConfig | None:
        for version in self._do_tenant(tenant_id):
            if version.is_active:
                return version.config
        return None

    async def list_versions(
        self, tenant_id: str, *, limit: int, offset: int = 0
    ) -> AiConfigVersionPage:
        ordenadas = sorted(self._do_tenant(tenant_id), key=lambda v: v.version, reverse=True)
        # Uma a mais que o limite, como no par real: é ela que responde "tem mais?".
        recorte = ordenadas[offset : offset + limit + 1]
        return AiConfigVersionPage(versions=recorte[:limit], has_more=len(recorte) > limit)

    async def create_version(
        self, *, tenant_id: str, created_by: str, config: AiConfig
    ) -> AiConfigVersion:
        do_tenant = self._do_tenant(tenant_id)
        proxima = max((v.version for v in do_tenant), default=0) + 1
        conta = await self.users.find_by_id(created_by)

        criada = AiConfigVersion(
            id=str(uuid.uuid4()),
            version=proxima,
            is_active=True,
            created_at=datetime.now(UTC),
            created_by_email=conta.email if conta is not None else None,
            config=config,
        )
        self.versions.append(criada)
        self._tenants[criada.id] = tenant_id
        self._desativar_menos(tenant_id, criada.id)
        return criada

    async def activate(self, *, tenant_id: str, config_id: str) -> bool:
        if self._tenants.get(config_id) != tenant_id:
            # Id de outro tenant é indistinguível de inexistente — e é assim que o par
            # real se comporta, porque a policy simplesmente não enxerga a linha.
            return False
        self._desativar_menos(tenant_id, config_id)
        return True

    def _desativar_menos(self, tenant_id: str, ativa_id: str) -> None:
        """A troca sem nenhum instante com duas ativas nem com nenhuma.

        Aqui sai de graça, porque a lista é local. O par real precisa de **dois**
        comandos para chegar ao mesmo lugar (`_ativar`, em `ai_configs_postgres.py`): o
        `update … set is_active = (id = %s)` num comando só estoura no índice único
        parcial, que é verificado linha a linha. O que os dois garantem de fora é igual;
        o caminho, não."""
        for posicao, version in enumerate(self.versions):
            if self._tenants[version.id] != tenant_id:
                continue
            ativa = version.id == ativa_id
            if version.is_active != ativa:
                self.versions[posicao] = replace(version, is_active=ativa)

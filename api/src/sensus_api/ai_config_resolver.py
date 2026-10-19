"""Resolve a config de IA de um tenant, caindo nos defaults do ambiente.

Peça pequena e separada do adaptador de propósito: o adaptador fala do banco, e "qual é
o default quando ninguém configurou" é decisão de produto. Juntar as duas obrigaria o
teste de integração a conhecer a política de default, e o teste da política a subir um
banco.

É esta a dependência que o `RagUpstream` recebe — e não o diretório —, porque o
pipeline precisa de uma config **completa**, nunca de um `None` para tratar.
"""

from dataclasses import dataclass

from sensus_api.ai_configs import AiConfig, AiConfigDirectory


@dataclass(frozen=True)
class AiConfigResolver:
    directory: AiConfigDirectory
    defaults: AiConfig

    async def resolve(self, tenant_id: str) -> AiConfig:
        """A config ativa do tenant, ou os defaults do ambiente.

        Uma consulta por pergunta, **sem cache**. É uma leitura indexada por tenant ao
        lado de um embedding, uma busca híbrida e uma chamada de geração — ruído
        estatístico. E cache tem invalidação: errá-la serve a config velha logo depois
        de um rollback, que é exatamente o momento em que alguém está tentando
        consertar alguma coisa às pressas.
        """
        return await self.directory.active_for(tenant_id) or self.defaults

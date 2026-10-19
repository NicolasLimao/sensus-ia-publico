"""A porta única para o banco.

O isolamento entre tenants deixou de depender de o próximo `where` ser escrito certo:
toda conexão sai daqui, e sair daqui exige informar o tenant. A transação abre com
`set_config('sensus.tenant_id', <tenant>, true)`, e as policies da migração 003 leem
esse valor.

O terceiro argumento `true` é o que faz o efeito ser LOCAL à transação. Com `set` de
sessão, o valor sobreviveria à devolução da conexão ao pool e a requisição seguinte
poderia herdar o tenant da anterior — o vazamento silencioso que esta fatia existe
para eliminar.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

_SET_TENANT = "select set_config('sensus.tenant_id', %s, true)"


@dataclass(frozen=True)
class TenantScope:
    pool: AsyncConnectionPool

    @asynccontextmanager
    async def connection(self, tenant_id: str) -> AsyncIterator[AsyncConnection[Any]]:
        if not tenant_id:
            raise ValueError("tenant_id é obrigatório para abrir conexão")
        async with self.pool.connection() as connection, connection.transaction():
            await connection.execute(_SET_TENANT, (tenant_id,))
            yield connection

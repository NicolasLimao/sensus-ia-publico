"""A porta única para o banco.

O que este módulo compra: não existe caminho para uma conexão sem tenant. Um método
novo de repositório não CONSEGUE esquecer o contexto, porque não há de onde tirar
conexão sem informá-lo.
"""

from typing import Any

import pytest

from sensus_rag.tenant_scope import TenantScope

TENANT = "11111111-1111-1111-1111-111111111111"


class TransacaoEspia:
    """Objeto próprio, e não a conexão de novo: entrar na transação e tirar a conexão
    do pool são dois eventos distintos, e é a ordem entre eles que o teste afirma."""

    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def __aenter__(self) -> "TransacaoEspia":
        self.events.append("begin")
        return self

    async def __aexit__(self, *args: object) -> None:
        self.events.append("commit")


class ConexaoEspia:
    def __init__(self) -> None:
        self.executed: list[tuple[str, Any]] = []
        # Ordem dos eventos: é ela que diz se o `set_config` caiu DENTRO da transação.
        self.events: list[str] = []

    async def execute(self, sql: str, params: Any = None) -> None:
        self.executed.append((sql, params))
        self.events.append("execute")

    def transaction(self) -> TransacaoEspia:
        return TransacaoEspia(self.events)


class PoolEspiao:
    def __init__(self, conexao: ConexaoEspia) -> None:
        self.conexao = conexao

    def connection(self) -> "PoolEspiao":
        return self

    async def __aenter__(self) -> ConexaoEspia:
        self.conexao.events.append("checkout")
        return self.conexao

    async def __aexit__(self, *args: object) -> None:
        self.conexao.events.append("devolve")


async def test_a_conexao_sai_com_o_tenant_aplicado() -> None:
    conexao = ConexaoEspia()
    scope = TenantScope(pool=PoolEspiao(conexao))  # type: ignore[arg-type]

    async with scope.connection(TENANT):
        pass

    assert conexao.executed[0] == (
        "select set_config('sensus.tenant_id', %s, true)",
        (TENANT,),
    )


async def test_e_set_local_nao_set_de_sessao() -> None:
    """`set` de sessão sobrevive à devolução da conexão ao pool, e a requisição
    seguinte herdaria o tenant da anterior. Dois detalhes tornam o efeito local: o
    terceiro argumento `true` de `set_config` e o fato de a chamada acontecer DENTRO
    de uma transação já aberta. Fora dela, em autocommit, `set_config(..., true)`
    valeria por uma instrução só e a policy leria nulo."""
    conexao = ConexaoEspia()
    scope = TenantScope(pool=PoolEspiao(conexao))  # type: ignore[arg-type]

    async with scope.connection(TENANT):
        pass

    sql, _ = conexao.executed[0]
    assert sql.strip().endswith("true)")
    assert conexao.events == ["checkout", "begin", "execute", "commit", "devolve"]


async def test_tenant_vazio_e_recusado() -> None:
    """String vazia viraria GUC vazio, e a policy compararia com nada. Falhar aqui é
    melhor que devolver zero linhas silenciosamente lá na frente."""
    scope = TenantScope(pool=PoolEspiao(ConexaoEspia()))  # type: ignore[arg-type]

    with pytest.raises(ValueError):
        async with scope.connection(""):
            pass

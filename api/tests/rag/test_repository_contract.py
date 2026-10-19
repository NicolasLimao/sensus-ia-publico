"""Onde o duplo em memória e o Postgres real TÊM de concordar.

A revisão da fatia 2a achou dois defeitos com a mesma assinatura: um duplo de teste e o
componente real divergiam, e cada revisão via só um lado — o teste passava afirmando
algo que só era verdade no duplo. Este arquivo existe para prender essa categoria.

Nada aqui toca o banco: o que se testa é a parte do contrato que o repositório decide
ANTES de abrir conexão. O comportamento que depende do Postgres continua em
`test_postgres_repository.py`, sob `-m integration`.
"""

from typing import Any

import pytest

from sensus_rag.repository.memory import InMemoryChunkRepository
from sensus_rag.repository.postgres import PostgresChunkRepository

DIMENSIONS = 1024


class _EscopoProibido:
    """Escopo que grita se alguém tentar usá-lo. O ponto de cada teste daqui é que o
    repositório resolve o caso sem conexão nenhuma."""

    def connection(self, tenant_id: str) -> Any:
        raise AssertionError("o repositório abriu conexão num caminho que não precisa dela")


@pytest.mark.parametrize(
    "document_id",
    ["nao-existe", "", "123", "8231", "not-a-uuid", "'; drop table documents; --"],
)
async def test_apagar_com_id_malformado_devolve_false_nos_dois_repositorios(
    document_id: str,
) -> None:
    """Id que não é UUID é 'não encontrado', não 'serviço indisponível'.

    No Postgres, `where id = 'nao-existe'` levanta `invalid input syntax for type uuid`,
    que virava `RepositoryUnavailable` e chegava ao navegador como 503 — enquanto o
    duplo em memória devolvia `False` e a borda respondia 404. Mesma requisição, dois
    códigos, e o teste de rota só exercitava o duplo.
    """
    memoria = InMemoryChunkRepository()
    postgres = PostgresChunkRepository(scope=_EscopoProibido(), dimensions=DIMENSIONS)  # type: ignore[arg-type]

    assert await memoria.delete_document(tenant_id="t", document_id=document_id) is False
    assert await postgres.delete_document(tenant_id="t", document_id=document_id) is False


async def test_o_repositorio_real_so_conhece_o_banco_pelo_escopo_de_tenant() -> None:
    """Guarda estrutural: se voltar a existir um `pool` no repositório, existe de novo
    um caminho para conexão sem tenant — que é a brecha que a fatia 3a fecha."""
    campos = set(PostgresChunkRepository.__dataclass_fields__)

    assert "scope" in campos
    assert "pool" not in campos

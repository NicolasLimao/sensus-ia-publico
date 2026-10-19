"""Aplicador de migrações.

SQL versionado em `api/migrations/`, aplicado em ordem, com o que já rodou registrado
no próprio banco. Roda como comando: `python -m sensus_rag.migrate`.
"""

import asyncio
import sys
from pathlib import Path

import psycopg

from sensus_rag.config import get_rag_settings

# No Windows, o loop padrão do asyncio (ProactorEventLoop) não roda o psycopg em modo
# assíncrono: `asyncio.run(apply_migrations(...))` falha com InterfaceError antes de
# sequer tentar conectar. Este módulo é um comando de ponto de entrada — quem chama
# `main()` é o interpretador, não outro módulo — então mexer na política global de
# event loop aqui é seguro e correto, diferente do que seria em código de biblioteca.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"

_CONTROL = """
create table if not exists schema_migrations (
  version    text primary key,
  applied_at timestamptz not null default now()
)
"""


async def apply_migrations(database_url: str) -> list[str]:
    """Aplica o que falta e devolve os nomes aplicados nesta rodada."""
    applied: list[str] = []
    async with await psycopg.AsyncConnection.connect(database_url) as connection:
        async with connection.cursor() as cursor:
            await cursor.execute(_CONTROL)
            await cursor.execute("select version from schema_migrations")
            already_applied = {row[0] for row in await cursor.fetchall()}

        for file in sorted(MIGRATIONS.glob("*.sql")):
            if file.name in already_applied:
                continue
            async with connection.cursor() as cursor:
                await cursor.execute(file.read_text(encoding="utf-8"))
                await cursor.execute(
                    "insert into schema_migrations (version) values (%s)", (file.name,)
                )
            applied.append(file.name)
        await connection.commit()
    return applied


def main() -> int:
    url = get_rag_settings().database_url
    if not url:
        print("SENSUS_DATABASE_URL não configurada.", file=sys.stderr)
        return 1
    for name in asyncio.run(apply_migrations(url)):
        print(f"aplicada: {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

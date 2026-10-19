"""As tabelas de identidade existem depois da migração.

Integração: precisa do Postgres real, porque o que se está afirmando é sobre o schema.
"""

import uuid

import psycopg
import pytest

from sensus_rag.migrate import apply_migrations

pytestmark = pytest.mark.integration


async def test_a_migracao_cria_organizacoes_e_usuarios(database_url: str) -> None:
    await apply_migrations(database_url)

    async with await psycopg.AsyncConnection.connect(database_url) as connection:
        cursor = await connection.execute(
            "select table_name from information_schema.tables "
            "where table_schema = 'public' and table_name in ('organizations', 'users')"
        )
        encontradas = {row[0] for row in await cursor.fetchall()}

    assert encontradas == {"organizations", "users"}


async def test_a_migracao_cria_as_funcoes_de_login(database_url: str) -> None:
    """São a única fresta para ler `users` sem contexto de tenant — se sumirem, o
    login para de funcionar assim que a RLS entrar."""
    await apply_migrations(database_url)

    async with await psycopg.AsyncConnection.connect(database_url) as connection:
        cursor = await connection.execute(
            "select proname, prosecdef from pg_proc "
            "where proname in ('auth_user_by_email', 'auth_user_by_id')"
        )
        rows = await cursor.fetchall()

    assert {row[0] for row in rows} == {"auth_user_by_email", "auth_user_by_id"}
    assert all(row[1] is True for row in rows), "as funções precisam ser security definer"


async def test_o_cadastro_tambem_tem_funcao_de_fresta(database_url: str) -> None:
    """O INSERT em `users` acontece antes de existir tenant, e a policy da 003 o
    recusaria. Sem esta função, `POST /auth/signup` é 500 sob o papel de produção."""
    await apply_migrations(database_url)

    async with await psycopg.AsyncConnection.connect(database_url) as connection:
        cursor = await connection.execute(
            "select prosecdef from pg_proc where proname = 'auth_create_organization_with_admin'"
        )
        rows = await cursor.fetchall()

    assert rows, "a função de cadastro sumiu"
    assert all(row[0] is True for row in rows), "precisa ser security definer"


async def test_as_frestas_nao_sao_executaveis_por_qualquer_um(database_url: str) -> None:
    """Estas funções existem para FURAR a RLS: uma delas devolve hash de senha por
    e-mail. `create function` concede EXECUTE a PUBLIC por default, e no Supabase o
    schema `public` ainda carrega EXECUTE para `anon` — o papel do PostgREST anônimo.
    Aberto, é um primitivo de exfiltração publicado na internet.

    PUBLIC não é o único jeito de entrar: no Supabase, `alter default privileges ...
    grant all on functions to anon, authenticated, service_role` dá a cada função
    recém-criada grants EXPLÍCITOS para esses três papéis — e `revoke ... from
    public` não os remove, porque o grant não veio de PUBLIC. É por isso que a
    migração 005 revoga de cada um desses papéis por nome, e é por isso que este
    teste teria passado mesmo que uma migração futura esquecesse um deles do
    array — se checasse só `public` e `sensus_app`."""
    await apply_migrations(database_url)

    frestas = (
        "auth_user_by_email(text)",
        "auth_user_by_id(uuid)",
        "auth_create_organization_with_admin(text, text, text)",
        "auth_bump_session_epoch(uuid)",
        # A 006. `anon` é o papel das requisições NÃO autenticadas do Supabase: uma
        # fresta que ACEITA convite, alcançável pelo `anon`, é buraco aberto.
        "auth_invitation_by_token_hash(text)",
        # Dois argumentos desde a 008: o e-mail vem da linha do convite, não de quem
        # chama. Assinatura nova significou `drop` + `create`, e `drop` ZERA a ACL —
        # esta linha é o que pega o ritual de grants esquecido.
        "auth_accept_invitation(text, text)",
    )
    async with await psycopg.AsyncConnection.connect(database_url) as connection:
        # Espelha o guarda da própria migração: `anon`, `authenticated` e
        # `service_role` são papéis do Supabase e podem não existir num Postgres nu.
        cursor = await connection.execute(
            "select rolname from pg_roles "
            "where rolname in ('anon', 'authenticated', 'service_role')"
        )
        papeis_supabase = {row[0] for row in await cursor.fetchall()}

        for fresta in frestas:
            cursor = await connection.execute(
                "select has_function_privilege('public', %s, 'execute'), "
                "       has_function_privilege('sensus_app', %s, 'execute')",
                (fresta, fresta),
            )
            row = await cursor.fetchone()

            assert row is not None
            assert row[0] is False, f"{fresta} continua executável por PUBLIC"
            assert row[1] is True, f"{fresta} não é executável pelo sensus_app"

            for papel in papeis_supabase:
                cursor = await connection.execute(
                    "select has_function_privilege(%s, %s, 'execute')", (papel, fresta)
                )
                privilegio = await cursor.fetchone()

                assert privilegio is not None
                assert privilegio[0] is False, f"{fresta} continua executável por {papel}"


async def test_a_migracao_cria_o_contador_de_sessao(database_url: str) -> None:
    """É o contador que faz o logout valer no servidor: sem ele, o token continua
    valendo os 7 dias do prazo depois de a pessoa sair."""
    await apply_migrations(database_url)

    async with await psycopg.AsyncConnection.connect(database_url) as connection:
        cursor = await connection.execute(
            "select data_type, is_nullable, column_default "
            "from information_schema.columns "
            "where table_name = 'users' and column_name = 'session_epoch'"
        )
        row = await cursor.fetchone()

    assert row is not None, "a coluna session_epoch nao existe"
    assert row[0] == "integer"
    assert row[1] == "NO", "sem NOT NULL, uma conta antiga compararia contra nulo"
    assert row[2] == "0"


async def test_as_frestas_de_login_passam_a_devolver_o_contador(
    database_url: str,
) -> None:
    """Se a fresta não devolver o contador, `require_session` não tem com o que
    comparar e a invalidação vira decoração."""
    await apply_migrations(database_url)

    async with await psycopg.AsyncConnection.connect(database_url) as connection:
        # Referenciar a coluna é o teste: se ela não estiver no retorno, o Postgres
        # recusa a consulta em vez de devolver zero linhas.
        await connection.execute(
            "select session_epoch from auth_user_by_email('ninguem-3b@exemplo.invalido')"
        )
        await connection.execute(
            "select session_epoch from auth_user_by_id('00000000-0000-0000-0000-000000000000')"
        )


async def test_o_encerramento_de_sessao_tambem_tem_funcao_de_fresta(
    database_url: str,
) -> None:
    """O UPDATE em `users` precisaria de contexto de tenant que o logout não tem à
    mão sem acoplar o diretório ao TenantScope — a fresta explícita é a saída, no
    mesmo padrão das três da 3a."""
    await apply_migrations(database_url)

    async with await psycopg.AsyncConnection.connect(database_url) as connection:
        cursor = await connection.execute(
            "select prosecdef from pg_proc where proname = 'auth_bump_session_epoch'"
        )
        rows = await cursor.fetchall()

    assert rows, "a funcao de encerramento sumiu"
    assert all(row[0] is True for row in rows), "precisa ser security definer"


async def test_a_migracao_cria_a_tabela_de_convites(database_url: str) -> None:
    """Uso único e revogação exigem estado — é por isso que existe tabela, e não um
    token assinado sem estado (spec §3)."""
    await apply_migrations(database_url)

    async with await psycopg.AsyncConnection.connect(database_url) as connection:
        cursor = await connection.execute(
            "select column_name, is_nullable from information_schema.columns "
            "where table_name = 'invitations'"
        )
        colunas = {row[0]: row[1] for row in await cursor.fetchall()}

    assert colunas, "a tabela invitations nao existe"
    for obrigatoria in ("tenant_id", "token_hash", "created_by", "created_at", "expires_at"):
        assert colunas[obrigatoria] == "NO", f"{obrigatoria} precisa ser NOT NULL"
    # Os carimbos SÃO o estado do convite: nulo é "ainda não aconteceu". É por isso que
    # não existe coluna `status` — ela poderia discordar dos carimbos (spec §3).
    for carimbo in ("accepted_at", "revoked_at", "accepted_user_id"):
        assert colunas[carimbo] == "YES", f"{carimbo} precisa aceitar nulo"


async def test_o_hash_do_token_e_unico_global(database_url: str) -> None:
    """O convidado chega SEM tenant: a busca é só pelo hash. Único por tenant deixaria
    a busca ambígua justamente no caminho que não tem tenant para desempatar."""
    await apply_migrations(database_url)

    async with await psycopg.AsyncConnection.connect(database_url) as connection:
        cursor = await connection.execute(
            "select indexdef from pg_indexes where tablename = 'invitations'"
        )
        definicoes = [row[0] for row in await cursor.fetchall()]

    assert any("UNIQUE" in d and "token_hash" in d for d in definicoes), (
        "token_hash precisa de indice unico global"
    )


async def test_a_tabela_de_convites_tem_rls_ligada(database_url: str) -> None:
    """O lado do admin toca a tabela pela policy, e não por um `where` meu (spec §3).
    Sem a policy ligada, a decisão inteira vira decoração."""
    await apply_migrations(database_url)

    async with await psycopg.AsyncConnection.connect(database_url) as connection:
        cursor = await connection.execute(
            "select relrowsecurity from pg_class where relname = 'invitations'"
        )
        ligada = await cursor.fetchone()
        cursor = await connection.execute(
            "select policyname from pg_policies where tablename = 'invitations'"
        )
        policies = {row[0] for row in await cursor.fetchall()}

    assert ligada is not None and ligada[0] is True, "RLS desligada em invitations"
    assert policies == {"invitations_por_tenant"}


async def test_as_frestas_de_convite_existem_e_sao_security_definer(
    database_url: str,
) -> None:
    """O convidado não tem tenant nenhum — é a mesma justificativa das frestas do
    login. Sem elas, prévia e aceite seriam zero linhas sob a policy."""
    await apply_migrations(database_url)

    async with await psycopg.AsyncConnection.connect(database_url) as connection:
        cursor = await connection.execute(
            "select proname, prosecdef from pg_proc where proname in "
            "('auth_invitation_by_token_hash', 'auth_accept_invitation')"
        )
        rows = await cursor.fetchall()

    assert {row[0] for row in rows} == {
        "auth_invitation_by_token_hash",
        "auth_accept_invitation",
    }
    assert all(row[1] is True for row in rows), "as duas precisam ser security definer"


async def test_o_cadastro_devolve_o_session_epoch_da_coluna_e_nao_um_literal(
    database_url: str,
) -> None:
    """A correção estacionada pela 3b (spec §4.4). A função devolvia o literal `0`;
    hoje isso ACERTA por coincidência, porque a coluna tem default `0` e é a própria
    função que cria a linha — e passa a errar no instante em que um trigger ou um
    backfill tocar a coluna.

    O teste troca o default da coluna DENTRO de uma transação revertida: se a função
    lê a coluna, devolve 7; se devolve o literal, devolve 0. É a única forma de a
    diferença aparecer, e é por isso que ela não apareceu até agora.
    """
    await apply_migrations(database_url)
    email = f"epoch-{uuid.uuid4().hex}@exemplo.invalido"

    async with (
        await psycopg.AsyncConnection.connect(database_url) as connection,
        connection.transaction(force_rollback=True),
    ):
        await connection.execute("alter table users alter column session_epoch set default 7")
        cursor = await connection.execute(
            "select session_epoch from auth_create_organization_with_admin(%s, %s, %s)",
            ("Acme", email, "hash"),
        )
        row = await cursor.fetchone()

    assert row is not None
    assert row[0] == 7, "a funcao devolve um literal em vez de ler a coluna"

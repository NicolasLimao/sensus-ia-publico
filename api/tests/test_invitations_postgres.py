"""O diretório real de convites, contra o Postgres. As MESMAS asserções do duplo, para
que a divergência entre os dois apareça aqui e não em produção.

Conecta pelo `app_database_url` — o papel `sensus_app`, SEM bypass de RLS, que é o papel
que a aplicação usa. Pelo dono das tabelas, que ignora policy, os testes de isolamento
passariam sem a policy existir, e um `grant execute` esquecido nas frestas novas só
apareceria em produção.

Dois testes daqui não têm equivalente possível no duplo, e são a razão de este arquivo
existir: o aceite concorrente e o rollback do e-mail repetido são propriedades da
TRANSAÇÃO. Um duplo em Python os acerta por construção sem provar nada (spec §8).
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from psycopg_pool import AsyncConnectionPool

from sensus_api.invitations_postgres import PostgresInvitationDirectory
from sensus_api.users import Account, EmailAlreadyUsed
from sensus_api.users_postgres import PostgresUserDirectory
from sensus_rag.tenant_scope import TenantScope

pytestmark = pytest.mark.integration


def _hash() -> str:
    return uuid.uuid4().hex


def _email(prefixo: str) -> str:
    return f"{prefixo}-{uuid.uuid4().hex}@exemplo.invalido"


def _daqui_a(segundos: int) -> datetime:
    return datetime.now(UTC) + timedelta(seconds=segundos)


def _diretorios(
    pool: AsyncConnectionPool,
) -> tuple[PostgresUserDirectory, PostgresInvitationDirectory]:
    return (
        PostgresUserDirectory(pool=pool),
        PostgresInvitationDirectory(scope=TenantScope(pool=pool), pool=pool),
    )


async def _admin(users: PostgresUserDirectory, *, organizacao: str = "Acme") -> Account:
    return await users.create_organization_with_admin(
        organization_name=organizacao, email=_email("ana"), password_hash="hash"
    )


async def test_convite_criado_aparece_entre_os_pendentes(app_database_url: str) -> None:
    async with AsyncConnectionPool(app_database_url, open=False) as pool:
        await pool.open()
        users, invitations = _diretorios(pool)
        admin = await _admin(users)

        criado = await invitations.create(
            tenant_id=admin.tenant_id,
            created_by=admin.user_id,
            email=_email("convidada"),
            token_hash=_hash(),
            expires_at=_daqui_a(3600),
        )

        pendentes = await invitations.list_pending(tenant_id=admin.tenant_id)
        assert [p.id for p in pendentes] == [criado.id]
        # O banco carimba o `created_at`; o duplo usa `datetime.now(UTC)`. Os dois têm
        # de devolver datetime COM fuso, senão a comparação de prazo divergiria.
        assert criado.created_at.tzinfo is not None
        assert criado.expires_at.tzinfo is not None


async def test_aceitar_cria_member_na_organizacao_de_quem_convidou(
    app_database_url: str,
) -> None:
    async with AsyncConnectionPool(app_database_url, open=False) as pool:
        await pool.open()
        users, invitations = _diretorios(pool)
        admin = await _admin(users)
        token_hash = _hash()
        email = _email("bob")
        await invitations.create(
            tenant_id=admin.tenant_id,
            created_by=admin.user_id,
            email=email,
            token_hash=token_hash,
            expires_at=_daqui_a(3600),
        )

        conta = await invitations.accept(token_hash=token_hash, password_hash="hash-do-bob")

        assert conta is not None
        assert conta.role == "member"
        assert conta.tenant_id == admin.tenant_id
        assert conta.session_epoch == 0
        # E com o endereço DO CONVITE, que é a propriedade da 3e: não há parâmetro por
        # onde outro endereço entraria.
        assert conta.email == email
        # E a pessoa passa a existir para o login — é o que faz o convite valer de algo.
        assert await users.find_by_email(email) == conta


async def test_o_mesmo_link_nao_serve_duas_vezes(app_database_url: str) -> None:
    async with AsyncConnectionPool(app_database_url, open=False) as pool:
        await pool.open()
        users, invitations = _diretorios(pool)
        admin = await _admin(users)
        token_hash = _hash()
        convidada = _email("convidada")
        await invitations.create(
            tenant_id=admin.tenant_id,
            created_by=admin.user_id,
            email=convidada,
            token_hash=token_hash,
            expires_at=_daqui_a(3600),
        )
        primeira = await invitations.accept(token_hash=token_hash, password_hash="h")

        segunda = await invitations.accept(token_hash=token_hash, password_hash="h")

        assert segunda is None
        # E o segundo clique não criou uma segunda conta: desde a 3e o endereço vem do
        # convite, então o que resta provar é que a conta continua sendo a do primeiro.
        assert await users.find_by_email(convidada) == primeira


async def test_link_vencido_e_link_revogado_nao_sao_aceitos(app_database_url: str) -> None:
    async with AsyncConnectionPool(app_database_url, open=False) as pool:
        await pool.open()
        users, invitations = _diretorios(pool)
        admin = await _admin(users)
        vencido, revogado = _hash(), _hash()
        # Endereços distintos de propósito: convite novo para o MESMO endereço revoga o
        # pendente anterior (a substituição da 3e), e isso trocaria o motivo pelo qual o
        # primeiro link deixa de servir.
        await invitations.create(
            tenant_id=admin.tenant_id,
            created_by=admin.user_id,
            email=_email("vencida"),
            token_hash=vencido,
            expires_at=_daqui_a(-1),
        )
        criado = await invitations.create(
            tenant_id=admin.tenant_id,
            created_by=admin.user_id,
            email=_email("revogada"),
            token_hash=revogado,
            expires_at=_daqui_a(3600),
        )
        await invitations.revoke(tenant_id=admin.tenant_id, invitation_id=criado.id)

        assert await invitations.accept(token_hash=vencido, password_hash="h") is None
        assert await invitations.accept(token_hash=revogado, password_hash="h") is None
        assert await invitations.list_pending(tenant_id=admin.tenant_id) == []


async def test_dois_aceites_simultaneos_no_mesmo_link_e_um_so_ganha(
    app_database_url: str,
) -> None:
    """O consumo acontece DENTRO do `update`, não lido-e-escrito. Este teste é a razão
    de a função SQL ser escrita assim, e não há como um duplo em Python provar isto:
    lá, as duas chamadas nunca disputam de verdade a mesma linha.

    A segunda transação fica bloqueada no `update` até a primeira comitar, e então
    reavalia o `where` (READ COMMITTED) — encontra `accepted_at` preenchido e não
    casa. Por isso uma volta com conta e a outra com `None`.
    """
    async with AsyncConnectionPool(app_database_url, min_size=2, open=False) as pool:
        await pool.open()
        users, invitations = _diretorios(pool)
        admin = await _admin(users)
        token_hash = _hash()
        await invitations.create(
            tenant_id=admin.tenant_id,
            created_by=admin.user_id,
            email=_email("convidada"),
            token_hash=token_hash,
            expires_at=_daqui_a(3600),
        )

        primeira, segunda = await asyncio.gather(
            invitations.accept(token_hash=token_hash, password_hash="h"),
            invitations.accept(token_hash=token_hash, password_hash="h"),
        )

        vencedoras = [conta for conta in (primeira, segunda) if conta is not None]
        assert len(vencedoras) == 1, "o mesmo convite criou duas contas"


async def test_email_que_ja_tem_conta_nao_queima_o_convite(app_database_url: str) -> None:
    """A propriedade que só o banco pode provar: o 23505 do `insert` derruba a função
    inteira, o `accepted_at` volta atrás e o link CONTINUA usável. Se a função tivesse
    sido escrita em dois passos com commit no meio, este é o único teste que reprovaria.
    """
    async with AsyncConnectionPool(app_database_url, open=False) as pool:
        await pool.open()
        users, invitations = _diretorios(pool)
        admin = await _admin(users)
        token_hash = _hash()
        # O convite é PARA o endereço que já tem conta — desde a 3e é o convite que
        # decide o endereço, então é assim que a colisão chega ao diretório. Na
        # aplicação a conta colidente é de OUTRA organização: na própria, a borda barra
        # antes (409 `already_member`).
        await invitations.create(
            tenant_id=admin.tenant_id,
            created_by=admin.user_id,
            email=admin.email,
            token_hash=token_hash,
            expires_at=_daqui_a(3600),
        )

        with pytest.raises(EmailAlreadyUsed):
            await invitations.accept(token_hash=token_hash, password_hash="h")

        carimbos = await invitations.stamps_by_token_hash(token_hash)
        assert carimbos is not None
        assert carimbos.accepted_at is None, "uma tentativa que falhou consumiu o convite"
        # E o link continua VIVO, não só não-consumido: quem repete a tentativa esbarra
        # de novo na colisão, em vez de receber o `None` de convite usado.
        with pytest.raises(EmailAlreadyUsed):
            await invitations.accept(token_hash=token_hash, password_hash="h")


async def test_revogar_convite_ja_aceito_nao_tem_efeito(app_database_url: str) -> None:
    async with AsyncConnectionPool(app_database_url, open=False) as pool:
        await pool.open()
        users, invitations = _diretorios(pool)
        admin = await _admin(users)
        token_hash = _hash()
        criado = await invitations.create(
            tenant_id=admin.tenant_id,
            created_by=admin.user_id,
            email=_email("convidada"),
            token_hash=token_hash,
            expires_at=_daqui_a(3600),
        )
        await invitations.accept(token_hash=token_hash, password_hash="h")

        await invitations.revoke(tenant_id=admin.tenant_id, invitation_id=criado.id)

        carimbos = await invitations.stamps_by_token_hash(token_hash)
        assert carimbos is not None
        assert carimbos.revoked_at is None


async def test_revogar_o_que_nao_existe_nao_estoura(app_database_url: str) -> None:
    async with AsyncConnectionPool(app_database_url, open=False) as pool:
        await pool.open()
        users, invitations = _diretorios(pool)
        admin = await _admin(users)

        await invitations.revoke(tenant_id=admin.tenant_id, invitation_id=str(uuid.uuid4()))


async def test_um_admin_nao_lista_nem_revoga_o_convite_do_outro(
    app_database_url: str,
) -> None:
    """A RLS isolando, e não um `where` meu: `list_pending` e `revoke` não filtram por
    tenant no SQL — quem filtra é a policy da 006."""
    async with AsyncConnectionPool(app_database_url, open=False) as pool:
        await pool.open()
        users, invitations = _diretorios(pool)
        dono = await _admin(users, organizacao="Acme")
        vizinho = await _admin(users, organizacao="Globex")
        token_hash = _hash()
        criado = await invitations.create(
            tenant_id=dono.tenant_id,
            created_by=dono.user_id,
            email=_email("convidada"),
            token_hash=token_hash,
            expires_at=_daqui_a(3600),
        )

        # Metade negativa: o vizinho não vê e não consegue revogar.
        assert await invitations.list_pending(tenant_id=vizinho.tenant_id) == []
        await invitations.revoke(tenant_id=vizinho.tenant_id, invitation_id=criado.id)

        # Metade positiva: o dono continua vendo, e o convite continua valendo. Sem
        # ela, uma policy que negasse tudo para todo mundo passaria neste teste.
        assert [p.id for p in await invitations.list_pending(tenant_id=dono.tenant_id)] == [
            criado.id
        ]
        carimbos = await invitations.stamps_by_token_hash(token_hash)
        assert carimbos is not None and carimbos.revoked_at is None
        assert carimbos.organization_name == "Acme"


async def test_sem_contexto_de_tenant_a_aplicacao_nao_ve_convite_nenhum(
    app_database_url: str,
) -> None:
    """O teste que de fato prova a policy — o mesmo desenho de `tests/rag/test_rls.py`.
    Uma consulta crua, fora do `TenantScope`, sem GUC nenhum: `current_setting(...,
    true)` devolve nulo, a comparação não casa, e o resultado é ZERO linhas em vez das
    linhas de todo mundo. Falha fechada.
    """
    async with AsyncConnectionPool(app_database_url, open=False) as pool:
        await pool.open()
        users, invitations = _diretorios(pool)
        admin = await _admin(users)
        await invitations.create(
            tenant_id=admin.tenant_id,
            created_by=admin.user_id,
            email=_email("convidada"),
            token_hash=_hash(),
            expires_at=_daqui_a(3600),
        )

        async with pool.connection() as connection:
            cursor = await connection.execute("select count(*) from invitations")
            sem_contexto = await cursor.fetchone()

        com_contexto = len(await invitations.list_pending(tenant_id=admin.tenant_id))

    assert sem_contexto is not None
    assert sem_contexto[0] == 0, "consulta sem contexto de tenant enxergou convites"
    assert com_contexto == 1


async def test_a_conexao_dos_convites_nao_ignora_rls(app_database_url: str) -> None:
    """Se a aplicação estivesse conectando como `postgres`, todo teste acima passaria
    por acidente — superusuário ignora policy. Este afirma o papel."""
    async with AsyncConnectionPool(app_database_url, open=False) as pool:
        await pool.open()
        async with pool.connection() as connection:
            cursor = await connection.execute(
                "select rolsuper or rolbypassrls from pg_roles where rolname = current_user"
            )
            row = await cursor.fetchone()

    assert row is not None
    assert row[0] is False, "a aplicação conecta com papel que ignora RLS"


async def test_token_desconhecido_nao_tem_carimbos(app_database_url: str) -> None:
    async with AsyncConnectionPool(app_database_url, open=False) as pool:
        await pool.open()
        _, invitations = _diretorios(pool)

        assert await invitations.stamps_by_token_hash(_hash()) is None


async def test_aceitar_token_desconhecido_devolve_none(app_database_url: str) -> None:
    async with AsyncConnectionPool(app_database_url, open=False) as pool:
        await pool.open()
        _, invitations = _diretorios(pool)

        assert await invitations.accept(token_hash=_hash(), password_hash="h") is None


async def test_convite_novo_revoga_o_pendente_do_mesmo_endereco(
    app_database_url: str,
) -> None:
    """A substituição, contra o banco: sem ela, "reenviar" deixa dois links vivos e a
    pessoa clica no que já não vale."""
    async with AsyncConnectionPool(app_database_url, open=False) as pool:
        await pool.open()
        users, invitations = _diretorios(pool)
        admin = await _admin(users)
        convidada = _email("convidada")
        antigo = _hash()
        await invitations.create(
            tenant_id=admin.tenant_id,
            created_by=admin.user_id,
            email=convidada,
            token_hash=antigo,
            expires_at=_daqui_a(7 * 24 * 3600),
        )

        novo = _hash()
        await invitations.create(
            tenant_id=admin.tenant_id,
            created_by=admin.user_id,
            email=convidada,
            token_hash=novo,
            expires_at=_daqui_a(7 * 24 * 3600),
        )

        carimbos_antigos = await invitations.stamps_by_token_hash(antigo)
        assert carimbos_antigos is not None and carimbos_antigos.revoked_at is not None
        carimbos_novos = await invitations.stamps_by_token_hash(novo)
        assert carimbos_novos is not None and carimbos_novos.revoked_at is None


async def test_convite_novo_nao_toca_o_de_outro_tenant_com_o_mesmo_endereco(
    app_database_url: str,
) -> None:
    """A substituição não leva `where tenant_id` — quem isola é a policy da 006. Este
    teste é o que impede a policy de virar decoração na operação nova: sem ela, convidar
    na Acme revogaria o convite que a Globex mandou para a MESMA pessoa.
    """
    async with AsyncConnectionPool(app_database_url, open=False) as pool:
        await pool.open()
        users, invitations = _diretorios(pool)
        acme = await _admin(users, organizacao="Acme")
        globex = await _admin(users, organizacao="Globex")
        convidada = _email("convidada")
        da_globex = _hash()
        await invitations.create(
            tenant_id=globex.tenant_id,
            created_by=globex.user_id,
            email=convidada,
            token_hash=da_globex,
            expires_at=_daqui_a(7 * 24 * 3600),
        )

        await invitations.create(
            tenant_id=acme.tenant_id,
            created_by=acme.user_id,
            email=convidada,
            token_hash=_hash(),
            expires_at=_daqui_a(7 * 24 * 3600),
        )

        carimbos = await invitations.stamps_by_token_hash(da_globex)
        assert carimbos is not None and carimbos.revoked_at is None


async def test_os_carimbos_trazem_o_endereco_do_convite(app_database_url: str) -> None:
    """É o que a prévia mostra: "você foi convidado como fulano@x". Vem da fresta de
    leitura, que a 008 teve de recriar para ganhar a coluna."""
    async with AsyncConnectionPool(app_database_url, open=False) as pool:
        await pool.open()
        users, invitations = _diretorios(pool)
        admin = await _admin(users, organizacao="Acme")
        convidada = _email("convidada")
        token_hash = _hash()
        await invitations.create(
            tenant_id=admin.tenant_id,
            created_by=admin.user_id,
            email=convidada,
            token_hash=token_hash,
            expires_at=_daqui_a(3600),
        )

        carimbos = await invitations.stamps_by_token_hash(token_hash)

        assert carimbos is not None
        assert carimbos.organization_name == "Acme"
        assert carimbos.email == convidada

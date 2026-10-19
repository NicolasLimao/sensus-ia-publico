"""O diretório real de config de IA, contra o Postgres.

As mesmas asserções do duplo valem aqui, e mais duas que só o banco dá — e que são a
razão deste arquivo existir:

- **uma ativa por tenant** garantida pelo índice único parcial, não pelo código;
- **isolamento pela policy**, com o adaptador sem nenhum `where tenant_id = %s`. Um
  `where` meu por cima esconderia uma policy quebrada, e o teste de leitura cruzada
  ficaria passando por engano (a lição que a 3c registrou).

Conecta pelo `sensus_app`, SEM bypass de RLS: pelo dono das tabelas a policy não se
aplica, e o teste de isolamento passaria sem provar nada.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator

import psycopg
import pytest
from psycopg_pool import AsyncConnectionPool

from sensus_api.ai_configs import AiConfig, ConcurrentConfigWrite
from sensus_api.ai_configs_postgres import PostgresAiConfigDirectory
from sensus_api.users_postgres import PostgresUserDirectory
from sensus_rag.tenant_scope import TenantScope

pytestmark = pytest.mark.integration


def _email(prefixo: str) -> str:
    return f"{prefixo}-{uuid.uuid4().hex}@exemplo.invalido"


def config(briefing: str = "", model: str = "gpt-4o-mini", top_k: int = 5) -> AiConfig:
    return AiConfig(
        briefing=briefing,
        generation_model=model,
        generation_top_k=top_k,
        max_context_chars=12_000,
        suggestions=("Do que trata a base?", "Quais são os prazos?"),
    )


@pytest.fixture
async def pool(app_database_url: str) -> AsyncIterator[AsyncConnectionPool]:
    async with AsyncConnectionPool(app_database_url, open=False) as pool:
        await pool.open()
        yield pool


@pytest.fixture
def configs(pool: AsyncConnectionPool) -> PostgresAiConfigDirectory:
    return PostgresAiConfigDirectory(scope=TenantScope(pool=pool))


@pytest.fixture
def users(pool: AsyncConnectionPool) -> PostgresUserDirectory:
    return PostgresUserDirectory(pool=pool)


async def _tenant(users: PostgresUserDirectory) -> tuple[str, str]:
    """Devolve `(tenant_id, user_id)` de uma organização recém-criada."""
    conta = await users.create_organization_with_admin(
        organization_name="Acme", email=_email("admin"), password_hash="h"
    )
    return conta.tenant_id, conta.user_id


class TestVersoes:
    async def test_tenant_sem_config_nao_tem_ativa_nem_versoes(
        self, configs: PostgresAiConfigDirectory, users: PostgresUserDirectory
    ) -> None:
        tenant, _ = await _tenant(users)

        assert await configs.active_for(tenant) is None
        assert (await configs.list_versions(tenant, limit=10)).versions == []

    async def test_a_primeira_versao_e_a_um_e_ja_nasce_ativa(
        self, configs: PostgresAiConfigDirectory, users: PostgresUserDirectory
    ) -> None:
        tenant, autor = await _tenant(users)

        criada = await configs.create_version(
            tenant_id=tenant, created_by=autor, config=config(briefing="Somos a Acme.")
        )

        assert criada.version == 1
        assert criada.is_active is True
        ativa = await configs.active_for(tenant)
        assert ativa is not None and ativa.briefing == "Somos a Acme."

    async def test_a_versao_seguinte_desativa_a_anterior(
        self, configs: PostgresAiConfigDirectory, users: PostgresUserDirectory
    ) -> None:
        tenant, autor = await _tenant(users)
        primeira = await configs.create_version(
            tenant_id=tenant, created_by=autor, config=config(briefing="um")
        )

        segunda = await configs.create_version(
            tenant_id=tenant, created_by=autor, config=config(briefing="dois")
        )

        assert segunda.version == 2
        pagina = await configs.list_versions(tenant, limit=10)
        estados = {v.id: v.is_active for v in pagina.versions}
        assert estados[segunda.id] is True
        assert estados[primeira.id] is False

    async def test_as_sugestoes_e_o_briefing_voltam_como_foram_gravados(
        self, configs: PostgresAiConfigDirectory, users: PostgresUserDirectory
    ) -> None:
        """`text[]` e texto com acento e quebra de linha atravessam o driver — é o tipo
        de coisa que só quebra contra o banco real."""
        tenant, autor = await _tenant(users)
        original = AiConfig(
            briefing="Somos a Acme.\nAtendemos condomínios.",
            generation_model="gpt-4o-mini",
            generation_top_k=7,
            max_context_chars=9_000,
            suggestions=("Qual é o prazo?", "Como faço a segunda via?"),
        )

        criada = await configs.create_version(tenant_id=tenant, created_by=autor, config=original)

        assert criada.config == original
        assert await configs.active_for(tenant) == original

    async def test_a_versao_registra_o_email_de_quem_criou(
        self, configs: PostgresAiConfigDirectory, users: PostgresUserDirectory
    ) -> None:
        conta = await users.create_organization_with_admin(
            organization_name="Acme", email=_email("ana"), password_hash="h"
        )

        criada = await configs.create_version(
            tenant_id=conta.tenant_id, created_by=conta.user_id, config=config()
        )

        assert criada.created_by_email == conta.email


class TestUmaAtivaPorTenant:
    async def test_o_banco_recusa_duas_ativas(
        self,
        configs: PostgresAiConfigDirectory,
        users: PostgresUserDirectory,
        pool: AsyncConnectionPool,
    ) -> None:
        """A invariante é do BANCO, não do código: um caminho futuro que ative sem
        desativar falha aqui em vez de criar a segunda verdade em silêncio."""
        tenant, autor = await _tenant(users)
        primeira = await configs.create_version(tenant_id=tenant, created_by=autor, config=config())
        await configs.create_version(tenant_id=tenant, created_by=autor, config=config())

        scope = TenantScope(pool=pool)
        with pytest.raises(psycopg.errors.UniqueViolation):
            async with scope.connection(tenant) as connection:
                await connection.execute(
                    "update ai_configs set is_active = true where id = %s",
                    (primeira.id,),
                )

    async def test_ativar_uma_antiga_e_rollback_e_nao_apaga_a_nova(
        self, configs: PostgresAiConfigDirectory, users: PostgresUserDirectory
    ) -> None:
        tenant, autor = await _tenant(users)
        primeira = await configs.create_version(
            tenant_id=tenant, created_by=autor, config=config(briefing="original")
        )
        ruim = await configs.create_version(
            tenant_id=tenant, created_by=autor, config=config(briefing="ruim")
        )

        assert await configs.activate(tenant_id=tenant, config_id=primeira.id) is True

        ativa = await configs.active_for(tenant)
        assert ativa is not None and ativa.briefing == "original"
        assert ruim.id in {v.id for v in (await configs.list_versions(tenant, limit=10)).versions}


class TestIsolamento:
    async def test_um_tenant_nao_enxerga_a_versao_do_outro(
        self, configs: PostgresAiConfigDirectory, users: PostgresUserDirectory
    ) -> None:
        """Quem isola é a POLICY: o adaptador não escreve `where tenant_id = %s`."""
        acme, autor_acme = await _tenant(users)
        globex, autor_globex = await _tenant(users)
        await configs.create_version(
            tenant_id=globex, created_by=autor_globex, config=config(briefing="do globex")
        )

        await configs.create_version(
            tenant_id=acme, created_by=autor_acme, config=config(briefing="da acme")
        )

        da_acme = (await configs.list_versions(acme, limit=10)).versions
        assert [v.config.briefing for v in da_acme] == ["da acme"]

    async def test_ativar_versao_de_outro_tenant_e_falso(
        self, configs: PostgresAiConfigDirectory, users: PostgresUserDirectory
    ) -> None:
        acme, _ = await _tenant(users)
        globex, autor_globex = await _tenant(users)
        do_globex = await configs.create_version(
            tenant_id=globex, created_by=autor_globex, config=config()
        )

        assert await configs.activate(tenant_id=acme, config_id=do_globex.id) is False

    async def test_ativar_no_meu_tenant_nao_desativa_o_do_outro(
        self, configs: PostgresAiConfigDirectory, users: PostgresUserDirectory
    ) -> None:
        acme, autor_acme = await _tenant(users)
        globex, autor_globex = await _tenant(users)
        await configs.create_version(
            tenant_id=globex, created_by=autor_globex, config=config(briefing="do globex")
        )
        minha = await configs.create_version(
            tenant_id=acme, created_by=autor_acme, config=config(briefing="da acme")
        )

        await configs.activate(tenant_id=acme, config_id=minha.id)

        ativa_do_globex = await configs.active_for(globex)
        assert ativa_do_globex is not None
        assert ativa_do_globex.briefing == "do globex"

    async def test_a_numeracao_de_versao_e_independente_entre_tenants(
        self, configs: PostgresAiConfigDirectory, users: PostgresUserDirectory
    ) -> None:
        acme, autor_acme = await _tenant(users)
        globex, autor_globex = await _tenant(users)
        await configs.create_version(tenant_id=acme, created_by=autor_acme, config=config())
        await configs.create_version(tenant_id=acme, created_by=autor_acme, config=config())

        primeira_do_globex = await configs.create_version(
            tenant_id=globex, created_by=autor_globex, config=config()
        )

        assert primeira_do_globex.version == 1


class TestAtivarOQueNaoExiste:
    """O par real dos testes de borda do duplo.

    O duplo devolve `False` para qualquer id que ele não conheça, e a borda responde 404.
    Contra o Postgres, um id que não é UUID chega como texto a uma coluna `uuid`: sem
    guarda, o banco levanta `invalid input syntax for type uuid` e a rota vira 500. Este
    arquivo existe para que o duplo e o real não divirjam em silêncio — a lição de 2a.
    """

    async def test_ativar_id_inexistente_e_falso(
        self, configs: PostgresAiConfigDirectory, users: PostgresUserDirectory
    ) -> None:
        acme, _ = await _tenant(users)

        assert await configs.activate(tenant_id=acme, config_id=str(uuid.uuid4())) is False

    async def test_ativar_id_MALFORMADO_e_falso_e_nao_levanta(
        self, configs: PostgresAiConfigDirectory, users: PostgresUserDirectory
    ) -> None:
        acme, _ = await _tenant(users)

        assert await configs.activate(tenant_id=acme, config_id="nao-existe") is False


async def _esperar_bloqueio(pool: AsyncConnectionPool, comando: str) -> None:
    """Espera a segunda operação chegar a ESPERAR pela primeira, no banco.

    Sem esta espera o teste não testaria concorrência nenhuma: se a transação adiantada
    comitasse antes, a segunda leria o estado já pronto e passaria sem conflito — verde
    por acidente, provando o caminho feliz com nome de corrida. `comando` é um pedaço do
    SQL que a bloqueada está executando, porque há mais de uma corrida a esperar.
    """
    for _ in range(200):
        async with pool.connection() as espia:
            cursor = await espia.execute(
                """
                select count(*) from pg_stat_activity
                where wait_event_type = 'Lock'
                  and query ilike %s
                """,
                (f"%{comando}%",),
            )
            linha = await cursor.fetchone()
        if linha is not None and linha[0] > 0:
            return
        await asyncio.sleep(0.05)
    raise AssertionError("a segunda gravação nunca chegou a esperar pelo índice único")


class TestGravacaoSimultanea:
    """Dois admins do mesmo tenant salvando no mesmo instante.

    A corrida é real e o duplo em memória não a reproduz — ele roda numa tarefa só e não
    suspende no meio de `create_version`. Quem recusa é o `unique (tenant_id, version)`,
    e o que este teste fixa é que a recusa chega ao chamador como
    `ConcurrentConfigWrite`, e não como uma `UniqueViolation` crua que a borda
    traduziria em 500.
    """

    async def test_a_gravacao_que_perde_a_corrida_levanta_erro_de_concorrencia(
        self,
        configs: PostgresAiConfigDirectory,
        users: PostgresUserDirectory,
        pool: AsyncConnectionPool,
    ) -> None:
        tenant, autor = await _tenant(users)
        scope = TenantScope(pool=pool)

        async with scope.connection(tenant) as adiantada:
            # O outro admin, no meio do próprio salvamento: a versão 1 já inserida e a
            # transação ainda aberta, invisível para quem vier depois.
            await adiantada.execute(
                """
                insert into ai_configs (
                    tenant_id, version, generation_model, generation_top_k,
                    max_context_chars, created_by
                ) values (%s, 1, 'gpt-4o-mini', 5, 12000, %s)
                """,
                (tenant, autor),
            )
            perdedora = asyncio.create_task(
                configs.create_version(tenant_id=tenant, created_by=autor, config=config())
            )
            await _esperar_bloqueio(pool, "insert into ai_configs")
        # Fora do `with` a adiantada comitou, e a perdedora acorda dentro do 23505.

        with pytest.raises(ConcurrentConfigWrite):
            await perdedora

    async def test_a_corrida_perdida_nao_deixa_meia_versao_gravada(
        self,
        configs: PostgresAiConfigDirectory,
        users: PostgresUserDirectory,
        pool: AsyncConnectionPool,
    ) -> None:
        """A perdedora ativa a versão antes de descobrir o conflito? Não: a transação
        inteira do `TenantScope` é desfeita, e o tenant fica com a versão da vencedora,
        ativa, e com nenhuma sobra."""
        tenant, autor = await _tenant(users)
        scope = TenantScope(pool=pool)

        async with scope.connection(tenant) as adiantada:
            await adiantada.execute(
                """
                insert into ai_configs (
                    tenant_id, version, generation_model, generation_top_k,
                    max_context_chars, created_by, is_active
                ) values (%s, 1, 'gpt-4o-mini', 5, 12000, %s, true)
                """,
                (tenant, autor),
            )
            perdedora = asyncio.create_task(
                configs.create_version(tenant_id=tenant, created_by=autor, config=config())
            )
            await _esperar_bloqueio(pool, "insert into ai_configs")

        with pytest.raises(ConcurrentConfigWrite):
            await perdedora

        versoes = (await configs.list_versions(tenant, limit=10)).versions
        assert [(v.version, v.is_active) for v in versoes] == [(1, True)]


class TestPaginacao:
    """O `limit`/`offset` no banco, e não na memória do processo: carregar a coleção
    inteira para descartar tudo menos uma página seria pagar o custo que a paginação
    existe para evitar."""

    async def test_a_pagina_vem_da_mais_nova_para_a_mais_antiga_e_diz_que_ha_mais(
        self, configs: PostgresAiConfigDirectory, users: PostgresUserDirectory
    ) -> None:
        tenant, autor = await _tenant(users)
        for numero in range(3):
            await configs.create_version(
                tenant_id=tenant, created_by=autor, config=config(briefing=str(numero))
            )

        pagina = await configs.list_versions(tenant, limit=2)

        assert [v.version for v in pagina.versions] == [3, 2]
        assert pagina.has_more is True

    async def test_o_offset_alcanca_a_ultima_pagina_e_ela_nao_tem_mais(
        self, configs: PostgresAiConfigDirectory, users: PostgresUserDirectory
    ) -> None:
        tenant, autor = await _tenant(users)
        for numero in range(3):
            await configs.create_version(
                tenant_id=tenant, created_by=autor, config=config(briefing=str(numero))
            )

        pagina = await configs.list_versions(tenant, limit=2, offset=2)

        assert [v.version for v in pagina.versions] == [1]
        assert pagina.has_more is False

    async def test_a_versao_do_outro_tenant_nao_conta_como_proxima_pagina(
        self, configs: PostgresAiConfigDirectory, users: PostgresUserDirectory
    ) -> None:
        """A linha a mais que responde "tem mais?" é lida sob a policy: a do vizinho
        nunca chega a ser candidata."""
        acme, autor_acme = await _tenant(users)
        globex, autor_globex = await _tenant(users)
        await configs.create_version(tenant_id=acme, created_by=autor_acme, config=config())
        await configs.create_version(tenant_id=globex, created_by=autor_globex, config=config())

        pagina = await configs.list_versions(acme, limit=1)

        assert len(pagina.versions) == 1
        assert pagina.has_more is False


class TestAtivacaoSimultanea:
    """Dois admins fazendo rollback no mesmo instante.

    A mesma corrida da gravação, no vizinho: o índice único parcial só deixa UMA versão
    ativa por tenant, e `activate` chega nele em dois comandos. Se a segunda transação
    não enxergar a linha que a primeira acabou de ativar, ela ativa a sua por cima e o
    índice recusa — um 500 pelo mesmo motivo que a gravação simultânea tinha.
    """

    async def test_a_ativacao_que_perde_a_corrida_levanta_erro_de_concorrencia(
        self,
        configs: PostgresAiConfigDirectory,
        users: PostgresUserDirectory,
        pool: AsyncConnectionPool,
    ) -> None:
        tenant, autor = await _tenant(users)
        primeira = await configs.create_version(
            tenant_id=tenant, created_by=autor, config=config(briefing="um")
        )
        segunda = await configs.create_version(
            tenant_id=tenant, created_by=autor, config=config(briefing="dois")
        )
        await configs.create_version(
            tenant_id=tenant, created_by=autor, config=config(briefing="tres")
        )

        scope = TenantScope(pool=pool)
        async with scope.connection(tenant) as adiantada:
            # O outro admin, no meio do próprio rollback: os dois comandos de `_ativar`
            # já rodaram e a transação ainda não comitou.
            await adiantada.execute("update ai_configs set is_active = false where is_active")
            await adiantada.execute(
                "update ai_configs set is_active = true where id = %s", (primeira.id,)
            )
            perdedora = asyncio.create_task(
                configs.activate(tenant_id=tenant, config_id=segunda.id)
            )
            await _esperar_bloqueio(pool, "update ai_configs")

        with pytest.raises(ConcurrentConfigWrite):
            await perdedora

        # A invariante do banco fica de pé, e quem venceu foi quem comitou primeiro.
        ativas = [
            v.version
            for v in (await configs.list_versions(tenant, limit=10)).versions
            if v.is_active
        ]
        assert ativas == [1]

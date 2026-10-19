"""O duplo do diretório de config de IA.

O que NÃO está aqui: a garantia de "uma ativa por tenant" sob concorrência e o
isolamento pela policy. As duas são propriedades do BANCO — o duplo as acerta por
construção sem provar nada — e ficam em `test_ai_configs_postgres.py`.
"""

import pytest

from sensus_api.ai_configs import AiConfig
from sensus_api.ai_configs_memory import InMemoryAiConfigDirectory
from sensus_api.users_memory import InMemoryUserDirectory

TENANT = "t-acme"
OUTRO = "t-globex"


def config(briefing: str = "", model: str = "gpt-4o-mini") -> AiConfig:
    return AiConfig(
        briefing=briefing,
        generation_model=model,
        generation_top_k=5,
        max_context_chars=12_000,
        suggestions=("Do que trata a base?",),
    )


@pytest.fixture
def users() -> InMemoryUserDirectory:
    return InMemoryUserDirectory()


@pytest.fixture
def configs(users: InMemoryUserDirectory) -> InMemoryAiConfigDirectory:
    return InMemoryAiConfigDirectory(users=users)


class TestSemConfig:
    async def test_tenant_novo_nao_tem_config_ativa(
        self, configs: InMemoryAiConfigDirectory
    ) -> None:
        """`None`, e não um default: quem sabe qual é o default é o resolvedor."""
        assert await configs.active_for(TENANT) is None

    async def test_tenant_novo_nao_tem_versao_nenhuma(
        self, configs: InMemoryAiConfigDirectory
    ) -> None:
        assert (await configs.list_versions(TENANT, limit=10)).versions == []


class TestCriarVersao:
    async def test_a_primeira_versao_e_a_um_e_ja_nasce_ativa(
        self, configs: InMemoryAiConfigDirectory
    ) -> None:
        criada = await configs.create_version(
            tenant_id=TENANT, created_by="u-1", config=config(briefing="Somos a Acme.")
        )

        assert criada.version == 1
        assert criada.is_active is True
        ativa = await configs.active_for(TENANT)
        assert ativa is not None and ativa.briefing == "Somos a Acme."

    async def test_a_versao_seguinte_desativa_a_anterior(
        self, configs: InMemoryAiConfigDirectory
    ) -> None:
        primeira = await configs.create_version(
            tenant_id=TENANT, created_by="u-1", config=config(briefing="primeiro")
        )

        segunda = await configs.create_version(
            tenant_id=TENANT, created_by="u-1", config=config(briefing="segundo")
        )

        assert segunda.version == 2
        pagina = await configs.list_versions(TENANT, limit=10)
        versoes = {v.id: v.is_active for v in pagina.versions}
        assert versoes[segunda.id] is True
        assert versoes[primeira.id] is False

    async def test_a_versao_e_por_tenant_e_nao_global(
        self, configs: InMemoryAiConfigDirectory
    ) -> None:
        """O admin lê "versão 3" como a terceira da organização dele."""
        await configs.create_version(tenant_id=TENANT, created_by="u-1", config=config())

        primeira_do_outro = await configs.create_version(
            tenant_id=OUTRO, created_by="u-2", config=config()
        )

        assert primeira_do_outro.version == 1

    async def test_salvar_nao_altera_a_versao_anterior(
        self, configs: InMemoryAiConfigDirectory
    ) -> None:
        """Versões são imutáveis: é o que permite o rollback significar alguma coisa."""
        primeira = await configs.create_version(
            tenant_id=TENANT, created_by="u-1", config=config(briefing="original")
        )

        await configs.create_version(
            tenant_id=TENANT, created_by="u-1", config=config(briefing="mudado")
        )

        pagina = await configs.list_versions(TENANT, limit=10)
        guardada = next(v for v in pagina.versions if v.id == primeira.id)
        assert guardada.config.briefing == "original"

    async def test_a_listagem_vem_da_mais_nova_para_a_mais_antiga(
        self, configs: InMemoryAiConfigDirectory
    ) -> None:
        await configs.create_version(tenant_id=TENANT, created_by="u-1", config=config())
        await configs.create_version(tenant_id=TENANT, created_by="u-1", config=config())
        await configs.create_version(tenant_id=TENANT, created_by="u-1", config=config())

        versoes = (await configs.list_versions(TENANT, limit=10)).versions

        assert [v.version for v in versoes] == [3, 2, 1]

    async def test_a_versao_registra_o_email_de_quem_criou(
        self, configs: InMemoryAiConfigDirectory, users: InMemoryUserDirectory
    ) -> None:
        conta = await users.create_organization_with_admin(
            organization_name="Acme", email="ana@acme.com", password_hash="h"
        )

        criada = await configs.create_version(
            tenant_id=TENANT, created_by=conta.user_id, config=config()
        )

        assert criada.created_by_email == "ana@acme.com"


class TestAtivar:
    async def test_ativar_uma_antiga_e_rollback_e_nao_apaga_a_nova(
        self, configs: InMemoryAiConfigDirectory
    ) -> None:
        primeira = await configs.create_version(
            tenant_id=TENANT, created_by="u-1", config=config(briefing="original")
        )
        terceira_ignorada = await configs.create_version(
            tenant_id=TENANT, created_by="u-1", config=config(briefing="ruim")
        )

        assert await configs.activate(tenant_id=TENANT, config_id=primeira.id) is True

        ativa = await configs.active_for(TENANT)
        assert ativa is not None and ativa.briefing == "original"
        # A versão ruim continua existindo: rollback não é apagar.
        pagina = await configs.list_versions(TENANT, limit=10)
        assert terceira_ignorada.id in {v.id for v in pagina.versions}

    async def test_ativar_id_inexistente_e_falso(self, configs: InMemoryAiConfigDirectory) -> None:
        assert await configs.activate(tenant_id=TENANT, config_id="nao-existe") is False

    async def test_ativar_versao_de_OUTRO_tenant_e_falso(
        self, configs: InMemoryAiConfigDirectory
    ) -> None:
        """Indistinguível de inexistente, de propósito: a borda responde 404 nos dois,
        como o `DELETE /documents/{id}` já faz desde a 2a."""
        do_outro = await configs.create_version(tenant_id=OUTRO, created_by="u-2", config=config())

        assert await configs.activate(tenant_id=TENANT, config_id=do_outro.id) is False

    async def test_ativar_nao_toca_na_config_do_outro_tenant(
        self, configs: InMemoryAiConfigDirectory
    ) -> None:
        do_outro = await configs.create_version(
            tenant_id=OUTRO, created_by="u-2", config=config(briefing="do outro")
        )
        minha = await configs.create_version(
            tenant_id=TENANT, created_by="u-1", config=config(briefing="minha")
        )

        await configs.activate(tenant_id=TENANT, config_id=minha.id)

        ativa_do_outro = await configs.active_for(OUTRO)
        assert ativa_do_outro is not None and ativa_do_outro.briefing == "do outro"
        # Relido do diretório, e não do objeto devolvido na criação: aquele é um
        # instantâneo congelado e diria `True` mesmo se a desativação tivesse vazado
        # para o outro tenant — teste que passa pelo motivo errado.
        pagina = await configs.list_versions(OUTRO, limit=10)
        relida = next(v for v in pagina.versions if v.id == do_outro.id)
        assert relida.is_active is True


class TestForGeneration:
    def test_o_recorte_leva_briefing_modelo_e_teto_e_deixa_o_top_k(self) -> None:
        """`top_k` governa a BUSCA, que acontece antes da geração. Mandá-lo ao gerador
        convidaria alguém a reinterpretá-lo lá dentro."""
        recorte = config(briefing="Somos a Acme.", model="modelo-x").for_generation()

        assert recorte.model == "modelo-x"
        assert recorte.briefing == "Somos a Acme."
        assert recorte.max_context_chars == 12_000
        assert not hasattr(recorte, "generation_top_k")


class TestPaginacao:
    """O histórico cresce sem teto — uma linha por edição — e a listagem inteira carrega
    o config completo de cada versão. Quem edita muito pagaria a coleção inteira a cada
    abertura do painel."""

    async def test_a_listagem_traz_no_maximo_o_limite_pedido(
        self, configs: InMemoryAiConfigDirectory
    ) -> None:
        for numero in range(5):
            await configs.create_version(
                tenant_id=TENANT, created_by="u-1", config=config(briefing=str(numero))
            )

        pagina = await configs.list_versions(TENANT, limit=2)

        assert [v.version for v in pagina.versions] == [5, 4]

    async def test_o_offset_continua_de_onde_a_pagina_anterior_parou(
        self, configs: InMemoryAiConfigDirectory
    ) -> None:
        for numero in range(5):
            await configs.create_version(
                tenant_id=TENANT, created_by="u-1", config=config(briefing=str(numero))
            )

        pagina = await configs.list_versions(TENANT, limit=2, offset=2)

        assert [v.version for v in pagina.versions] == [3, 2]

    async def test_ha_mais_quando_sobrou_versao_depois_da_pagina(
        self, configs: InMemoryAiConfigDirectory
    ) -> None:
        for numero in range(3):
            await configs.create_version(
                tenant_id=TENANT, created_by="u-1", config=config(briefing=str(numero))
            )

        assert (await configs.list_versions(TENANT, limit=2)).has_more is True

    async def test_nao_ha_mais_quando_a_pagina_termina_no_fim(
        self, configs: InMemoryAiConfigDirectory
    ) -> None:
        """Exatamente o limite, e nada depois: `has_more` falso. É o caso em que o
        botão "carregar mais" apareceria sem ter o que carregar."""
        for numero in range(2):
            await configs.create_version(
                tenant_id=TENANT, created_by="u-1", config=config(briefing=str(numero))
            )

        assert (await configs.list_versions(TENANT, limit=2)).has_more is False

    async def test_a_pagina_nao_conta_a_versao_do_outro_tenant(
        self, configs: InMemoryAiConfigDirectory
    ) -> None:
        await configs.create_version(tenant_id=TENANT, created_by="u-1", config=config())
        await configs.create_version(tenant_id=OUTRO, created_by="u-2", config=config())

        pagina = await configs.list_versions(TENANT, limit=1)

        assert pagina.has_more is False

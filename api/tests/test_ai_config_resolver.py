"""O resolvedor: a única peça que conhece os dois lados — a config do tenant e os
defaults do ambiente.

Existe separado do adaptador de propósito. O adaptador fala do banco; "qual é o
default quando ninguém configurou" é decisão de produto, e o teste do banco não deveria
precisar conhecê-la.
"""

import pytest

from sensus_api.ai_config_resolver import AiConfigResolver
from sensus_api.ai_configs import AiConfig
from sensus_api.ai_configs_memory import InMemoryAiConfigDirectory
from sensus_api.users_memory import InMemoryUserDirectory

TENANT = "t-acme"

DEFAULTS = AiConfig(
    briefing="",
    generation_model="gpt-4o-mini",
    generation_top_k=5,
    max_context_chars=12_000,
    suggestions=("Do que trata a base?",),
)


@pytest.fixture
def directory() -> InMemoryAiConfigDirectory:
    return InMemoryAiConfigDirectory(users=InMemoryUserDirectory())


@pytest.fixture
def resolver(directory: InMemoryAiConfigDirectory) -> AiConfigResolver:
    return AiConfigResolver(directory=directory, defaults=DEFAULTS)


async def test_sem_config_resolve_para_os_defaults(resolver: AiConfigResolver) -> None:
    assert await resolver.resolve(TENANT) == DEFAULTS


async def test_com_config_resolve_para_a_ativa(
    resolver: AiConfigResolver, directory: InMemoryAiConfigDirectory
) -> None:
    do_tenant = AiConfig(
        briefing="Somos a Acme.",
        generation_model="modelo-x",
        generation_top_k=8,
        max_context_chars=9_000,
        suggestions=("Qual é o prazo?",),
    )
    await directory.create_version(tenant_id=TENANT, created_by="u-1", config=do_tenant)

    assert await resolver.resolve(TENANT) == do_tenant


async def test_a_config_de_um_tenant_nao_alcanca_o_outro(
    resolver: AiConfigResolver, directory: InMemoryAiConfigDirectory
) -> None:
    await directory.create_version(
        tenant_id=TENANT,
        created_by="u-1",
        config=AiConfig(
            briefing="Somos a Acme.",
            generation_model="modelo-x",
            generation_top_k=8,
            max_context_chars=9_000,
            suggestions=(),
        ),
    )

    assert await resolver.resolve("t-globex") == DEFAULTS


async def test_a_resolucao_nao_e_cacheada(
    resolver: AiConfigResolver, directory: InMemoryAiConfigDirectory
) -> None:
    """Sem cache é decisão, não esquecimento: cache tem invalidação, e invalidação
    errada serve a config velha logo depois do rollback — o pior momento possível."""
    primeira = await directory.create_version(
        tenant_id=TENANT,
        created_by="u-1",
        config=AiConfig(
            briefing="original",
            generation_model="m",
            generation_top_k=5,
            max_context_chars=12_000,
            suggestions=(),
        ),
    )
    await directory.create_version(
        tenant_id=TENANT,
        created_by="u-1",
        config=AiConfig(
            briefing="ruim",
            generation_model="m",
            generation_top_k=5,
            max_context_chars=12_000,
            suggestions=(),
        ),
    )
    assert (await resolver.resolve(TENANT)).briefing == "ruim"

    await directory.activate(tenant_id=TENANT, config_id=primeira.id)

    assert (await resolver.resolve(TENANT)).briefing == "original"

"""Duplo do diretório de convites.

O par real vive em `invitations_postgres.py` e é exercitado contra o Postgres em
`tests/test_invitations_postgres.py`, nas MESMAS asserções. A armadilha aqui é
específica e está registrada na spec (§8): uso único e rollback do e-mail repetido são
propriedades da TRANSAÇÃO no par real, e este duplo os acerta por construção — um `if`
em Python. Todo teste deste arquivo tem equivalente de integração.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from sensus_api.invitations_memory import InMemoryInvitationDirectory
from sensus_api.users import Account, EmailAlreadyUsed
from sensus_api.users_memory import InMemoryUserDirectory


def _hash() -> str:
    """Um hash qualquer, distinto a cada chamada. O duplo não interpreta o valor."""
    return uuid.uuid4().hex


def _daqui_a(segundos: int) -> datetime:
    return datetime.now(UTC) + timedelta(seconds=segundos)


async def _admin(
    users: InMemoryUserDirectory, *, organizacao: str = "Acme", email: str = "ana@acme.com"
) -> Account:
    return await users.create_organization_with_admin(
        organization_name=organizacao, email=email, password_hash="hash"
    )


def _diretorios() -> tuple[InMemoryUserDirectory, InMemoryInvitationDirectory]:
    users = InMemoryUserDirectory()
    return users, InMemoryInvitationDirectory(users=users)


async def test_convite_criado_aparece_entre_os_pendentes() -> None:
    users, invitations = _diretorios()
    admin = await _admin(users)

    criado = await invitations.create(
        tenant_id=admin.tenant_id,
        created_by=admin.user_id,
        email="bob@acme.com",
        token_hash=_hash(),
        expires_at=_daqui_a(3600),
    )

    pendentes = await invitations.list_pending(tenant_id=admin.tenant_id)
    assert [p.id for p in pendentes] == [criado.id]
    assert pendentes[0].expires_at == criado.expires_at


async def test_aceitar_cria_member_na_organizacao_de_quem_convidou() -> None:
    """O caminho inteiro da fatia. `member`, e na organização do admin — não numa nova."""
    users, invitations = _diretorios()
    admin = await _admin(users)
    token_hash = _hash()
    await invitations.create(
        tenant_id=admin.tenant_id,
        created_by=admin.user_id,
        email="bob@acme.com",
        token_hash=token_hash,
        expires_at=_daqui_a(3600),
    )

    conta = await invitations.accept(token_hash=token_hash, password_hash="hash-do-bob")

    assert conta is not None
    assert conta.role == "member"
    assert conta.tenant_id == admin.tenant_id
    assert conta.session_epoch == 0
    # E a pessoa passa a existir para o login, que é o que faz o convite valer de algo.
    assert await users.find_by_email("bob@acme.com") == conta


async def test_o_mesmo_link_nao_serve_duas_vezes() -> None:
    users, invitations = _diretorios()
    admin = await _admin(users)
    token_hash = _hash()
    await invitations.create(
        tenant_id=admin.tenant_id,
        created_by=admin.user_id,
        email="bob@acme.com",
        token_hash=token_hash,
        expires_at=_daqui_a(3600),
    )
    primeira = await invitations.accept(token_hash=token_hash, password_hash="h")

    segunda = await invitations.accept(token_hash=token_hash, password_hash="h")

    assert segunda is None
    # E o segundo clique nao criou uma segunda conta: desde a 3e nao ha como o segundo
    # aceite chegar com outro endereco, entao o que resta provar e que a conta continua
    # sendo a do primeiro.
    assert await users.find_by_email("bob@acme.com") == primeira


async def test_link_vencido_nao_e_aceito() -> None:
    users, invitations = _diretorios()
    admin = await _admin(users)
    token_hash = _hash()
    await invitations.create(
        tenant_id=admin.tenant_id,
        created_by=admin.user_id,
        email="bob@acme.com",
        token_hash=token_hash,
        expires_at=_daqui_a(-1),
    )

    assert await invitations.accept(token_hash=token_hash, password_hash="h") is None


async def test_link_revogado_nao_e_aceito() -> None:
    users, invitations = _diretorios()
    admin = await _admin(users)
    token_hash = _hash()
    criado = await invitations.create(
        tenant_id=admin.tenant_id,
        created_by=admin.user_id,
        email="bob@acme.com",
        token_hash=token_hash,
        expires_at=_daqui_a(3600),
    )

    await invitations.revoke(tenant_id=admin.tenant_id, invitation_id=criado.id)

    assert await invitations.accept(token_hash=token_hash, password_hash="h") is None
    assert await invitations.list_pending(tenant_id=admin.tenant_id) == []


async def test_email_que_ja_tem_conta_nao_queima_o_convite() -> None:
    """A propriedade que o par real garante por rollback da transação, e que este duplo
    tem de reproduzir: a tentativa falha e o link CONTINUA usável."""
    users, invitations = _diretorios()
    admin = await _admin(users)
    token_hash = _hash()
    # O convite e PARA o endereco que ja tem conta - desde a 3e e o convite que decide o
    # endereco, entao e assim que a colisao chega ao diretorio. Na aplicacao a conta
    # colidente e de OUTRA organizacao: na propria, a borda barra antes (409
    # `already_member`).
    await invitations.create(
        tenant_id=admin.tenant_id,
        created_by=admin.user_id,
        email="ana@acme.com",
        token_hash=token_hash,
        expires_at=_daqui_a(3600),
    )

    with pytest.raises(EmailAlreadyUsed):
        await invitations.accept(token_hash=token_hash, password_hash="h")

    carimbos = await invitations.stamps_by_token_hash(token_hash)
    assert carimbos is not None
    assert carimbos.accepted_at is None, "o convite foi consumido por uma tentativa que falhou"
    # E o link continua VIVO, nao so nao-consumido: quem repete a tentativa esbarra de
    # novo na colisao, em vez de receber o `None` de convite usado.
    with pytest.raises(EmailAlreadyUsed):
        await invitations.accept(token_hash=token_hash, password_hash="h")


async def test_revogar_convite_ja_aceito_nao_tem_efeito() -> None:
    """Revogação só morde o pendente. Não existe "desfazer" uma pessoa que já entrou."""
    users, invitations = _diretorios()
    admin = await _admin(users)
    token_hash = _hash()
    criado = await invitations.create(
        tenant_id=admin.tenant_id,
        created_by=admin.user_id,
        email="bob@acme.com",
        token_hash=token_hash,
        expires_at=_daqui_a(3600),
    )
    await invitations.accept(token_hash=token_hash, password_hash="h")

    await invitations.revoke(tenant_id=admin.tenant_id, invitation_id=criado.id)

    carimbos = await invitations.stamps_by_token_hash(token_hash)
    assert carimbos is not None
    assert carimbos.revoked_at is None


async def test_revogar_o_que_nao_existe_nao_estoura() -> None:
    """A borda promete 204 idempotente."""
    users, invitations = _diretorios()
    admin = await _admin(users)

    await invitations.revoke(tenant_id=admin.tenant_id, invitation_id=str(uuid.uuid4()))


async def test_um_tenant_nao_lista_nem_revoga_o_convite_do_outro() -> None:
    users, invitations = _diretorios()
    dono = await _admin(users, organizacao="Acme", email="ana@acme.com")
    vizinho = await _admin(users, organizacao="Globex", email="bob@globex.com")
    token_hash = _hash()
    criado = await invitations.create(
        tenant_id=dono.tenant_id,
        created_by=dono.user_id,
        email="convidada@acme.com",
        token_hash=token_hash,
        expires_at=_daqui_a(3600),
    )

    assert await invitations.list_pending(tenant_id=vizinho.tenant_id) == []
    await invitations.revoke(tenant_id=vizinho.tenant_id, invitation_id=criado.id)

    # A metade positiva: o dono continua vendo o próprio, e o convite continua valendo.
    # Sem ela, um duplo que negasse tudo para todo mundo passaria no teste.
    assert [p.id for p in await invitations.list_pending(tenant_id=dono.tenant_id)] == [criado.id]
    carimbos = await invitations.stamps_by_token_hash(token_hash)
    assert carimbos is not None and carimbos.revoked_at is None


async def test_os_carimbos_trazem_o_nome_da_organizacao() -> None:
    """É o que a prévia mostra: "Você foi convidado para <organização>"."""
    users, invitations = _diretorios()
    admin = await _admin(users, organizacao="Acme")
    token_hash = _hash()
    await invitations.create(
        tenant_id=admin.tenant_id,
        created_by=admin.user_id,
        email="bob@acme.com",
        token_hash=token_hash,
        expires_at=_daqui_a(3600),
    )

    carimbos = await invitations.stamps_by_token_hash(token_hash)

    assert carimbos is not None
    assert carimbos.organization_name == "Acme"
    # E o endereco, desde a 3e: e o que deixa a previa dizer "voce foi convidado como
    # bob@acme.com" em vez de pedir fe.
    assert carimbos.email == "bob@acme.com"


async def test_token_desconhecido_nao_tem_carimbos() -> None:
    _, invitations = _diretorios()

    assert await invitations.stamps_by_token_hash(_hash()) is None


async def test_aceitar_token_desconhecido_devolve_none() -> None:
    _, invitations = _diretorios()

    assert await invitations.accept(token_hash=_hash(), password_hash="h") is None


async def test_a_conta_nasce_com_o_email_do_convite() -> None:
    """O vinculo, e ele e estrutural: nao ha parametro por onde outro endereco
    entraria."""
    users, invitations = _diretorios()
    admin = await _admin(users)
    await invitations.create(
        tenant_id=admin.tenant_id,
        created_by=admin.user_id,
        email="convidada@acme.com",
        token_hash="hash-1",
        expires_at=_daqui_a(7 * 24 * 3600),
    )

    conta = await invitations.accept(token_hash="hash-1", password_hash="nova")

    assert conta is not None
    assert conta.email == "convidada@acme.com"
    assert conta.role == "member"


async def test_convite_novo_revoga_o_pendente_do_mesmo_endereco() -> None:
    """Sem isto, reenviar deixa dois links vivos e a pessoa clica no que ja nao vale."""
    users, invitations = _diretorios()
    admin = await _admin(users)
    futuro = _daqui_a(7 * 24 * 3600)
    await invitations.create(
        tenant_id=admin.tenant_id,
        created_by=admin.user_id,
        email="convidada@acme.com",
        token_hash="hash-1",
        expires_at=futuro,
    )

    await invitations.create(
        tenant_id=admin.tenant_id,
        created_by=admin.user_id,
        email="convidada@acme.com",
        token_hash="hash-2",
        expires_at=futuro,
    )

    antigo = await invitations.stamps_by_token_hash("hash-1")
    assert antigo is not None and antigo.revoked_at is not None
    novo = await invitations.stamps_by_token_hash("hash-2")
    assert novo is not None and novo.revoked_at is None

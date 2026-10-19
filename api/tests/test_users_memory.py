"""Duplo do diretório de usuários. O par real vive em `users.py` e é exercitado
contra o Postgres na Task 5, na MESMA asserção — divergência entre duplo e real é a
categoria de defeito que mais escapou à revisão neste projeto."""

import uuid

import pytest

from sensus_api.users import Account, EmailAlreadyUsed, account_from_row
from sensus_api.users_memory import InMemoryUserDirectory


async def test_cadastro_cria_organizacao_e_admin() -> None:
    directory = InMemoryUserDirectory()

    account = await directory.create_organization_with_admin(
        organization_name="Acme", email="ana@acme.com", password_hash="hash"
    )

    assert account.role == "admin"
    assert account.tenant_id
    assert await directory.find_by_email("ana@acme.com") == account
    assert await directory.find_by_id(account.user_id) == account


async def test_email_repetido_e_recusado() -> None:
    directory = InMemoryUserDirectory()
    await directory.create_organization_with_admin(
        organization_name="Acme", email="ana@acme.com", password_hash="hash"
    )

    with pytest.raises(EmailAlreadyUsed):
        await directory.create_organization_with_admin(
            organization_name="Outra", email="ana@acme.com", password_hash="hash"
        )


async def test_email_e_normalizado_para_minusculas() -> None:
    """Senão a mesma pessoa cria duas contas com `Ana@` e `ana@`."""
    directory = InMemoryUserDirectory()
    await directory.create_organization_with_admin(
        organization_name="Acme", email="Ana@Acme.com", password_hash="hash"
    )

    assert await directory.find_by_email("ana@acme.com") is not None


async def test_email_desconhecido_devolve_none() -> None:
    assert await InMemoryUserDirectory().find_by_email("ninguem@acme.com") is None


async def test_id_desconhecido_devolve_none() -> None:
    assert await InMemoryUserDirectory().find_by_id("nao-existe") is None


async def test_cada_cadastro_ganha_organizacao_propria() -> None:
    """Duas empresas que se cadastram não podem cair no mesmo tenant."""
    directory = InMemoryUserDirectory()

    uma = await directory.create_organization_with_admin(
        organization_name="Acme", email="ana@acme.com", password_hash="h"
    )
    outra = await directory.create_organization_with_admin(
        organization_name="Globex", email="bob@globex.com", password_hash="h"
    )

    assert uma.tenant_id != outra.tenant_id


async def test_conta_nova_comeca_com_contador_zerado() -> None:
    directory = InMemoryUserDirectory()

    account = await directory.create_organization_with_admin(
        organization_name="Acme", email="ana@acme.com", password_hash="hash"
    )

    assert account.session_epoch == 0


async def test_encerrar_sessoes_incrementa_o_contador() -> None:
    """É o incremento que invalida o token: ele carrega o valor de quando foi
    assinado, e `require_session` compara com o de agora."""
    directory = InMemoryUserDirectory()
    account = await directory.create_organization_with_admin(
        organization_name="Acme", email="ana@acme.com", password_hash="hash"
    )

    await directory.bump_session_epoch(account.user_id)

    depois = await directory.find_by_id(account.user_id)
    assert depois is not None
    assert depois.session_epoch == account.session_epoch + 1


async def test_encerrar_sessoes_aparece_tambem_na_busca_por_email() -> None:
    """As duas leituras servem o login e o `require_session`; uma delas ficar para
    trás seria uma sessão encerrada que ainda entra."""
    directory = InMemoryUserDirectory()
    account = await directory.create_organization_with_admin(
        organization_name="Acme", email="ana@acme.com", password_hash="hash"
    )

    await directory.bump_session_epoch(account.user_id)

    por_email = await directory.find_by_email("ana@acme.com")
    assert por_email is not None
    assert por_email.session_epoch == 1


async def test_encerrar_sessoes_de_quem_nao_existe_nao_estoura() -> None:
    """O logout chama isto com o id que veio de um token de assinatura válida; se a
    conta foi apagada nesse meio-tempo, sair não pode virar 500."""
    await InMemoryUserDirectory().bump_session_epoch("nao-existe")


async def test_o_duplo_guarda_o_nome_da_organizacao() -> None:
    """A prévia do convite mostra o nome da organização; no par real ele vem do `join`
    com `organizations`. Sem guardá-lo, o duplo não teria o que mostrar."""
    directory = InMemoryUserDirectory()

    account = await directory.create_organization_with_admin(
        organization_name="Acme", email="ana@acme.com", password_hash="hash"
    )

    assert directory.organization_names[account.tenant_id] == "Acme"


async def test_criar_membro_entra_na_organizacao_existente_como_member() -> None:
    """`create_member` só existe no duplo: no par real o insert do membro acontece
    DENTRO de `auth_accept_invitation` (migração 006)."""
    directory = InMemoryUserDirectory()
    admin = await directory.create_organization_with_admin(
        organization_name="Acme", email="ana@acme.com", password_hash="hash"
    )

    membro = await directory.create_member(
        tenant_id=admin.tenant_id, email="Bob@Acme.com", password_hash="hash-do-bob"
    )

    assert membro.role == "member"
    assert membro.tenant_id == admin.tenant_id
    assert membro.session_epoch == 0
    # Minúsculas, como no cadastro: senão a mesma pessoa vira duas contas.
    assert membro.email == "bob@acme.com"
    assert await directory.find_by_email("bob@acme.com") == membro


async def test_criar_membro_com_email_que_ja_tem_conta_e_recusado() -> None:
    directory = InMemoryUserDirectory()
    admin = await directory.create_organization_with_admin(
        organization_name="Acme", email="ana@acme.com", password_hash="hash"
    )

    with pytest.raises(EmailAlreadyUsed):
        await directory.create_member(
            tenant_id=admin.tenant_id, email="ana@acme.com", password_hash="h"
        )


class TestAccountFromRow:
    """O mapeamento linha→`Account` é um lugar só, e é o mesmo para as cinco funções
    SQL que devolvem uma conta."""

    def test_monta_a_conta_na_ordem_das_colunas_das_funcoes_sql(self) -> None:
        row = (
            uuid.UUID("11111111-1111-1111-1111-111111111111"),
            uuid.UUID("22222222-2222-2222-2222-222222222222"),
            "alguem@exemplo.invalido",
            "$argon2id$hash",
            "member",
            3,
        )

        account = account_from_row(row)

        assert account == Account(
            user_id="11111111-1111-1111-1111-111111111111",
            tenant_id="22222222-2222-2222-2222-222222222222",
            email="alguem@exemplo.invalido",
            password_hash="$argon2id$hash",
            role="member",
            session_epoch=3,
        )

    def test_converte_os_uuids_do_postgres_para_str(self) -> None:
        """O psycopg devolve `uuid.UUID`; o resto do adaptador trata id como `str`, e
        um `UUID` vazando daqui só quebraria na comparação, longe da causa."""
        row = (uuid.uuid4(), uuid.uuid4(), "a@b.invalido", "h", "admin", 0)

        account = account_from_row(row)

        assert isinstance(account.user_id, str)
        assert isinstance(account.tenant_id, str)

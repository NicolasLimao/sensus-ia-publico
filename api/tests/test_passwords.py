"""Hash de senha. O algoritmo é argon2id — recomendação atual para senha nova."""

from sensus_api.passwords import burn_time, hash_password, verify_password


def test_a_senha_certa_verifica() -> None:
    assert verify_password("segredo-do-usuario", hash_password("segredo-do-usuario"))


def test_a_senha_errada_nao_verifica() -> None:
    assert not verify_password("outra-coisa", hash_password("segredo-do-usuario"))


def test_o_hash_nunca_e_a_senha() -> None:
    """Guarda contra o pior bug possível deste módulo: gravar a senha em claro."""
    assert "segredo-do-usuario" not in hash_password("segredo-do-usuario")


def test_duas_chamadas_dao_hashes_diferentes() -> None:
    """Sal por hash. Hashes iguais para senhas iguais entregariam, num vazamento,
    quais usuários compartilham senha."""
    assert hash_password("igual") != hash_password("igual")


def test_hash_corrompido_nao_estoura() -> None:
    """Linha adulterada no banco é `False`, não exceção que vira 500 no login."""
    assert not verify_password("qualquer", "isto-nao-e-um-hash-argon2")


def test_burn_time_nao_estoura() -> None:
    """Chamado no caminho do e-mail inexistente para igualar o tempo de resposta."""
    burn_time()

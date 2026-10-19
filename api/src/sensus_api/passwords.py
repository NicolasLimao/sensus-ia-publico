"""Hash de senha com argon2id.

`argon2-cffi` em vez de `passlib`: o algoritmo é o mesmo, e passlib acrescentaria uma
camada de escolha sobre uma decisão que já está tomada.
"""

from argon2 import PasswordHasher
from argon2.exceptions import (
    InvalidHashError,
    VerificationError,
    VerifyMismatchError,
)

_HASHER = PasswordHasher()

# Hash de uma senha que ninguém usa. Serve para o login gastar o mesmo tempo quando o
# e-mail não existe — sem isso, a diferença de tempo entrega quais e-mails têm conta.
_DUMMY = _HASHER.hash("nao-e-a-senha-de-ninguem")


def hash_password(password: str) -> str:
    return _HASHER.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _HASHER.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        # Senha errada e hash ilegível são a mesma resposta para quem chama: `False`.
        # Deixar a exceção subir transformaria linha adulterada no banco em 500.
        # `InvalidHashError` não descende de `VerificationError` — vem de `ValueError` —
        # e por isso precisa estar nomeada aqui, sob pena de o hash corrompido virar 500.
        return False


def burn_time() -> None:
    """Verifica um hash descartável. Chamado quando o e-mail não existe."""
    verify_password("nao-e-a-senha-de-ninguem", _DUMMY)

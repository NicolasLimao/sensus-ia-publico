"""O token de sessão — assinatura, prazo e geração de sessão.

Quem o token identifica, e o que essa pessoa pode fazer, é `principal.py`: resolvido a
cada requisição contra o diretório, nunca carregado no cookie. O que viaja aqui é só o
par (id, geração) — e a geração é o que torna o logout capaz de encerrar a sessão do
lado do servidor.
"""

from dataclasses import dataclass
from typing import Any

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

SESSION_COOKIE = "sensus_session"
# v2: a 3a assinava a string do id, sem contador. O salt novo derruba aqueles tokens
# na assinatura — invalidar o formato velho não pode depender de validação de forma,
# que é acidental.
_SALT = "sensus-session-v2"


@dataclass(frozen=True)
class SessionToken:
    user_id: str
    session_epoch: int


def sign_session(user_id: str, session_epoch: int, secret: str) -> str:
    return URLSafeTimedSerializer(secret, salt=_SALT).dumps(
        {"uid": user_id, "epoch": session_epoch}
    )


def read_session(token: str, secret: str, max_age_seconds: int) -> SessionToken | None:
    """O prazo mora no token, não no cookie. Cookie é do navegador; token é nosso."""
    try:
        payload: Any = URLSafeTimedSerializer(secret, salt=_SALT).loads(
            token, max_age=max_age_seconds
        )
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(payload, dict):
        return None
    user_id = payload.get("uid")
    epoch = payload.get("epoch")
    # `bool` é subclasse de `int` em Python: sem o descarte explícito, `True` viraria
    # a geração 1 e um token forjado passaria pela validação de forma.
    if not isinstance(user_id, str) or isinstance(epoch, bool) or not isinstance(epoch, int):
        return None
    return SessionToken(user_id=user_id, session_epoch=epoch)

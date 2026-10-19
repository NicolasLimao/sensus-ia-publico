"""Códigos de erro do Sensus.

O erro do upstream pode carregar stack, nome de tabela ou nome de cliente. Ele fica no
log do servidor; para o navegador vai só o código e uma frase nossa.
"""

from typing import Literal

from sensus_api.upstream.contract import (
    UpstreamBadRequest,
    UpstreamTimeout,
    UpstreamUnavailable,
)

ErrorCode = Literal["upstream_timeout", "service_unavailable", "bad_request"]

_MESSAGES: dict[ErrorCode, str] = {
    "upstream_timeout": "A busca demorou mais do que o esperado. Tente de novo.",
    "service_unavailable": "O serviço está indisponível agora. Tente de novo em instantes.",
    "bad_request": "Não foi possível processar essa pergunta.",
}


def code_for(exc: BaseException) -> tuple[ErrorCode, str]:
    """Mapeia qualquer exceção para (código, mensagem segura de exibir)."""
    if isinstance(exc, UpstreamTimeout | TimeoutError):
        code: ErrorCode = "upstream_timeout"
    elif isinstance(exc, UpstreamBadRequest):
        code = "bad_request"
    elif isinstance(exc, UpstreamUnavailable):
        code = "service_unavailable"
    else:
        code = "service_unavailable"
    return code, _MESSAGES[code]

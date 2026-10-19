"""Formatação de eventos SSE."""

import json
from collections.abc import Mapping


def format_event(event: str, data: Mapping[str, object]) -> str:
    """Monta um bloco SSE completo, já com a linha em branco final."""
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"

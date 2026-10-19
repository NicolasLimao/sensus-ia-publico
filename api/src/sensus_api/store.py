"""Histórico da conversa.

Em memória nesta fatia: a fatia 1 roda local e não tem banco. O Protocol é o que
permite trocar por persistência real na fatia 2 sem mexer nas rotas.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Literal, Protocol

from sensus_api.upstream.contract import Chunk

Role = Literal["user", "assistant"]
Rating = Literal["up", "down"]


@dataclass
class StoredMessage:
    id: str
    role: Role
    content: str
    sources: tuple[Chunk, ...] = ()
    chunks_used: int = 0
    feedback: Rating | None = None


class ConversationStore(Protocol):
    async def append(self, user_id: str, message: StoredMessage) -> None: ...

    async def history(self, user_id: str) -> list[StoredMessage]: ...

    async def set_feedback(self, user_id: str, message_id: str, rating: Rating) -> bool: ...


@dataclass
class InMemoryConversationStore:
    _threads: dict[str, list[StoredMessage]] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def append(self, user_id: str, message: StoredMessage) -> None:
        async with self._lock:
            self._threads.setdefault(user_id, []).append(message)

    async def history(self, user_id: str) -> list[StoredMessage]:
        async with self._lock:
            return list(self._threads.get(user_id, []))

    async def set_feedback(self, user_id: str, message_id: str, rating: Rating) -> bool:
        async with self._lock:
            for message in self._threads.get(user_id, []):
                if message.id == message_id and message.role == "assistant":
                    message.feedback = rating
                    return True
        return False

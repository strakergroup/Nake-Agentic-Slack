"""Session memory for the agent.

One session per Slack thread. State is JSON only (no pickle) so it can pause for
hours on a person's click and resume in a different process. Also holds the
agent's own duplicate-event guard (the app's dedupe only covers Enterprise Grid)
and the small map that lets a backend event find the session it belongs to.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Protocol

from .types import Session

PREFIX = "slack-ray-translator:agent:"
SESSION_TTL = 7 * 24 * 3600
TURN_TTL = 3600
QUOTE_TTL = 24 * 3600
LOCK_TTL = 120
MAX_MESSAGES = 60


def trim_history(messages: list[dict[str, Any]], limit: int = MAX_MESSAGES) -> list[dict[str, Any]]:
    """Drop the oldest messages, never splitting a tool_use from its tool_result.

    A safe cut point is a user message that carries no tool_result blocks.
    """
    if len(messages) <= limit:
        return messages

    def starts_clean(message: dict[str, Any]) -> bool:
        if message.get("role") != "user":
            return False
        content = message.get("content")
        if isinstance(content, str):
            return True
        return not any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content or [])

    for index in range(len(messages) - limit, len(messages)):
        if starts_clean(messages[index]):
            return messages[index:]
    return messages


class SessionStore(Protocol):
    async def load(self, key: str) -> Session | None: ...
    async def save(self, session: Session) -> None: ...
    async def claim_turn(self, event_id: str) -> bool: ...
    async def link_quote(self, quote_id: str, session_key: str) -> None: ...
    async def session_for_quote(self, quote_id: str) -> str | None: ...
    async def remember_open(self, team_id: str, channel_id: str, user_id: str, session_key: str) -> None: ...
    async def session_for_channel_user(self, team_id: str, channel_id: str, user_id: str) -> str | None: ...
    def lock(self, key: str) -> Any: ...


class InMemorySessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, str] = {}
        self._turns: set[str] = set()
        self._quotes: dict[str, str] = {}
        self._open: dict[str, str] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def load(self, key: str) -> Session | None:
        raw = self._sessions.get(key)
        return Session.from_json(json.loads(raw)) if raw else None

    async def save(self, session: Session) -> None:
        session.messages = trim_history(session.messages)
        self._sessions[session.key] = json.dumps(session.to_json())

    async def claim_turn(self, event_id: str) -> bool:
        if event_id in self._turns:
            return False
        self._turns.add(event_id)
        return True

    async def link_quote(self, quote_id: str, session_key: str) -> None:
        self._quotes[quote_id] = session_key

    async def session_for_quote(self, quote_id: str) -> str | None:
        return self._quotes.get(quote_id)

    async def remember_open(self, team_id: str, channel_id: str, user_id: str, session_key: str) -> None:
        self._open[f"{team_id}:{channel_id}:{user_id}"] = session_key

    async def session_for_channel_user(self, team_id: str, channel_id: str, user_id: str) -> str | None:
        return self._open.get(f"{team_id}:{channel_id}:{user_id}")

    @asynccontextmanager
    async def lock(self, key: str) -> AsyncIterator[None]:
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            yield


class RedisSessionStore:
    """`redis` is an asyncio Redis client, injected by the adapter."""

    def __init__(self, redis: Any) -> None:
        self._redis = redis

    async def load(self, key: str) -> Session | None:
        raw = await self._redis.get(f"{PREFIX}session:{key}")
        if not raw:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode()
        return Session.from_json(json.loads(raw))

    async def save(self, session: Session) -> None:
        session.messages = trim_history(session.messages)
        await self._redis.set(f"{PREFIX}session:{session.key}", json.dumps(session.to_json()), ex=SESSION_TTL)

    async def claim_turn(self, event_id: str) -> bool:
        return bool(await self._redis.set(f"{PREFIX}turn:{event_id}", "1", nx=True, ex=TURN_TTL))

    async def link_quote(self, quote_id: str, session_key: str) -> None:
        await self._redis.set(f"{PREFIX}quote:{quote_id}", session_key, ex=QUOTE_TTL)

    async def session_for_quote(self, quote_id: str) -> str | None:
        return _text(await self._redis.get(f"{PREFIX}quote:{quote_id}"))

    async def remember_open(self, team_id: str, channel_id: str, user_id: str, session_key: str) -> None:
        await self._redis.set(f"{PREFIX}open:{team_id}:{channel_id}:{user_id}", session_key, ex=SESSION_TTL)

    async def session_for_channel_user(self, team_id: str, channel_id: str, user_id: str) -> str | None:
        return _text(await self._redis.get(f"{PREFIX}open:{team_id}:{channel_id}:{user_id}"))

    def lock(self, key: str) -> Any:
        return self._redis.lock(f"{PREFIX}lock:{key}", timeout=LOCK_TTL, blocking_timeout=30)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    return value.decode() if isinstance(value, bytes) else str(value)

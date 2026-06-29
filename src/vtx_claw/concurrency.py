from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


class SessionLock:
    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}

    def lock(self, key: str) -> asyncio.Lock:
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    def prune(self, max_age: float = 3600) -> None:
        stale = [k for k, v in self._locks.items() if not v.locked()]
        for k in stale:
            self._locks.pop(k, None)


class GlobalSemaphore:
    def __init__(self, max_concurrent: int = 4) -> None:
        self._sem = asyncio.Semaphore(max_concurrent)

    async def acquire(self) -> asyncio.Semaphore:
        await self._sem.acquire()
        return self._sem


def compact_messages(
    messages: list[dict[str, Any]], token_budget: int = 4096, chars_per_token: float = 3.5
) -> list[dict[str, Any]]:
    budget = int(token_budget * chars_per_token)
    trimmed: list[dict[str, Any]] = []
    size = 0
    for m in reversed(messages):
        s = len(m.get("content", ""))
        if size + s > budget and trimmed:
            break
        trimmed.append(m)
        size += s
    return list(reversed(trimmed))

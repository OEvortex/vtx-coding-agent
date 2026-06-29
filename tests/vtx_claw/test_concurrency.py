from __future__ import annotations

import asyncio
import pytest

from vtx_claw.concurrency import SessionLock, GlobalSemaphore, compact_messages


@pytest.mark.asyncio
async def test_session_lock_prevents_double_run():
    lock = SessionLock()
    acquired = []

    async def task(name: str, delay: float) -> None:
        async with lock.lock("s1"):
            acquired.append(name)
            await asyncio.sleep(delay)

    await asyncio.gather(task("a", 0.05), task("b", 0.05))
    assert acquired == ["a", "b"]


@pytest.mark.asyncio
async def test_global_semaphore():
    sem = GlobalSemaphore(2)
    assert await sem.acquire() is not None


def test_compact_messages_trims_by_budget():
    messages = [{"role": "user", "content": "x" * 1000}] * 100
    out = compact_messages(messages, token_budget=100)
    assert len(out) < 100

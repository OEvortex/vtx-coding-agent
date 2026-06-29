from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import suppress

logger = logging.getLogger(__name__)


class HeartbeatRunner:
    def __init__(self, interval: float = 60.0) -> None:
        self._interval = interval
        self._running = False
        self._task: asyncio.Task[None] | None = None

    async def start(self, fn: Callable[[], None] | None = None) -> None:
        self._running = True
        self._task = asyncio.ensure_future(self._loop(fn))
        logger.info("Heartbeat started (interval %.1fs)", self._interval)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task

    async def _loop(self, fn: Callable[[], None] | None) -> None:
        while self._running:
            await asyncio.sleep(self._interval)
            if fn:
                try:
                    fn()
                except Exception:
                    logger.exception("Heartbeat callback failed")

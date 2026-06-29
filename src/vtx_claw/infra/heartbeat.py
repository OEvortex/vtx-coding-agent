from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

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
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self, fn: Callable[[], None] | None) -> None:
        while self._running:
            await asyncio.sleep(self._interval)
            if fn:
                try:
                    fn()
                except Exception:
                    logger.exception("Heartbeat callback failed")

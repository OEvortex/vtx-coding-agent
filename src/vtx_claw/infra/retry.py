from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)


class RetryPolicy:
    def __init__(self, tries: int = 3, delay: float = 1.0, jitter: float = 0.3) -> None:
        self.tries = tries
        self.delay = delay
        self.jitter = jitter

    async def call(self, fn: Callable[..., Awaitable[Any]], *args: Any, **kwargs: Any) -> Any:
        last: BaseException | None = None
        for attempt in range(self.tries):
            try:
                return await fn(*args, **kwargs)
            except Exception as exc:
                last = exc
                wait = self.delay * (2**attempt) + (time.time() % self.jitter)
                logger.warning("Attempt %d failed: %s; retrying in %.2fs", attempt, exc, wait)
                await asyncio.sleep(wait)
        if last is not None:
            raise last
        raise ValueError("RetryPolicy: tries must be greater than 0")

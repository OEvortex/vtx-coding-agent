from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)


class Deduper:
    def __init__(self, ttl: float = 300.0) -> None:
        self._ttl = ttl
        self._seen: dict[str, float] = {}

    def accept(self, key: str) -> bool:
        now = time.time()
        self._seen = {k: t for k, t in self._seen.items() if now - t < self._ttl}
        if key in self._seen:
            return False
        self._seen[key] = now
        return True

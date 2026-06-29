from __future__ import annotations

import logging
from collections import deque
from typing import Any

logger = logging.getLogger(__name__)


class DeliveryQueue:
    def __init__(self) -> None:
        self._queue: deque[dict[str, Any]] = deque()

    def put(self, item: dict[str, Any]) -> None:
        self._queue.append(item)

    def get(self) -> dict[str, Any] | None:
        if self._queue:
            return self._queue.popleft()
        return None

    def __len__(self) -> int:
        return len(self._queue)

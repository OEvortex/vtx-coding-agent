"""Fire-and-forget task spawning that survives garbage collection.

``asyncio.ensure_future(coro)`` with the result discarded is a real bug, not a
style nit: the event loop only holds a weak reference to a running task, so a
task nobody keeps can be collected partway through and silently stop. The
linters flag it for that reason.

Every task spawned here is held in a module-level set and dropped from it when
it finishes, so the strong reference lasts exactly as long as the task does.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any, TypeVar

log = logging.getLogger("mcp.tasks")

T = TypeVar("T")

# Strong references to in-flight background tasks.
_background: set[asyncio.Task] = set()


def spawn(coro: Coroutine[Any, Any, T], *, name: str | None = None) -> asyncio.Task:
    """Schedule ``coro`` and keep it alive until it completes."""
    task = asyncio.ensure_future(coro)
    if name is not None:
        task.set_name(name)
    _background.add(task)
    task.add_done_callback(_background.discard)
    return task


def _on_done(task: asyncio.Task) -> None:
    """Log a background task's failure instead of losing it to "never retrieved".

    Nothing awaits these tasks, so without this an exception would sit in the
    task until the loop's exception handler reported it as an unretrieved
    error -- pointing at the spawn site rather than the real cause.
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log.debug("background MCP task failed", exc_info=exc)


def spawn_logging(coro: Coroutine[Any, Any, T], *, name: str | None = None) -> asyncio.Task:
    """Like :func:`spawn`, but logs the task's exception when it fails."""
    task = spawn(coro, name=name)
    task.add_done_callback(_on_done)
    return task


__all__ = ["spawn", "spawn_logging"]

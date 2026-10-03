"""Concurrency control for ``Task``-tool sub-agents.

A model that fans out ten sub-agents in one turn would otherwise open ten
concurrent provider streams at once: the run is slower (rate limits, thrash),
and the TUI has nothing honest to show but a wall of spinners. This module caps
how many sub-agents may be *in flight* and parks the rest in a FIFO queue, so
"running" and "queued" are real counts the UI can render.

The scheduler is deliberately dumb — no priorities, no re-prioritisation, no
cross-process state. One asyncio loop, one queue, and a slot handed to
whichever queued sub-agent has waited longest.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import deque

log = logging.getLogger("agent.subagents")

#: Cap used when config cannot be read (headless callers, early boot).
DEFAULT_MAX_CONCURRENT = 4


class SubagentScheduler:
    """A FIFO admission queue for sub-agent runs."""

    def __init__(self, limit: int = DEFAULT_MAX_CONCURRENT) -> None:
        self._limit = max(0, int(limit))
        self._running = 0
        self._waiters: deque[asyncio.Future[None]] = deque()

    @property
    def limit(self) -> int:
        """Max simultaneous runs; ``0`` means unlimited."""
        return self._limit

    @property
    def running(self) -> int:
        return self._running

    @property
    def queued(self) -> int:
        """Sub-agents waiting for a slot."""
        return len(self._waiters)

    def admits_now(self) -> bool:
        return self._limit == 0 or self._running < self._limit

    async def acquire(self) -> None:
        """Block until a slot is free. Pairs with :meth:`release`."""
        if self.admits_now():
            self._running += 1
            return

        loop = asyncio.get_running_loop()
        waiter: asyncio.Future[None] = loop.create_future()
        self._waiters.append(waiter)
        try:
            await waiter
        except asyncio.CancelledError:
            with contextlib.suppress(ValueError):
                self._waiters.remove(waiter)
            if waiter.done() and not waiter.cancelled():
                # Cancelled after a slot was already handed over: pass it on
                # so the queue keeps moving.
                self._release_next()
            raise
        # The slot was reserved by whoever released it, so a fresh arrival
        # cannot barge ahead of this waiter while it resumes.

    def release(self) -> None:
        """Give this run's slot back, waking the longest-waiting sub-agent."""
        if self._running > 0:
            self._running -= 1
        self._release_next()

    def _release_next(self) -> None:
        while self._waiters:
            waiter = self._waiters.popleft()
            if waiter.done():
                # Cancelled while queued; skip it.
                continue
            self._running += 1
            waiter.set_result(None)
            return

    def set_limit(self, limit: int) -> None:
        """Re-size the cap, e.g. after a config reload.

        Shrinking never pre-empts a running sub-agent: it only holds new ones
        until the in-flight count drops under the new limit.
        """
        limit = max(0, int(limit))
        if limit == self._limit:
            return
        self._limit = limit
        self.drain()

    def drain(self) -> None:
        """Start queued sub-agents for every slot the limit now allows."""
        while self._waiters and self.admits_now():
            self._release_next()

    def reset(self) -> None:
        """Drop all queue state (session teardown / config reload)."""
        for waiter in self._waiters:
            if not waiter.done():
                waiter.cancel()
        self._waiters.clear()
        self._running = 0


_scheduler: SubagentScheduler | None = None


def get_scheduler() -> SubagentScheduler:
    """The process-wide scheduler, sized from ``task.max_concurrent``."""
    global _scheduler
    if _scheduler is None:
        _scheduler = SubagentScheduler(_configured_limit())
    return _scheduler


def set_limit(limit: int) -> None:
    """Re-size the shared scheduler after a config reload."""
    get_scheduler().set_limit(limit)


def reset_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.reset()
    _scheduler = None


def _configured_limit() -> int:
    try:
        from vtx.core.config import config

        return int(config.task.max_concurrent)
    except Exception:
        log.debug("sub-agent concurrency limit unavailable; using default", exc_info=True)
        return DEFAULT_MAX_CONCURRENT

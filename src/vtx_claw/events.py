"""Event system for vtx_claw — bridges vtx.core events with channel-level routing.

Keeps the async EventBus for internal pub/sub (channel messages, lifecycle)
while reusing :mod:`vtx.events` types for streaming and agent lifecycle
so the gateway and the TUI share a common vocabulary.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

from vtx.events import (
    AgentEndEvent,
    AgentStartEvent,
    ErrorEvent,
    TextDeltaEvent,
    TextEndEvent,
    ThinkingDeltaEvent,
    ThinkingEndEvent,
    ThinkingStartEvent,
    ToolEndEvent,
    ToolResultEvent,
    ToolStartEvent,
    TurnEndEvent,
    TurnStartEvent,
)

logger = logging.getLogger(__name__)

E = TypeVar("E", bound="Event")


# ── vtx_claw channel-level events (not in vtx.events) ──────────────────────


@dataclass
class Event:
    """Base event for vtx_claw's internal bus."""

    type: str
    timestamp: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class InboundEvent(Event):
    """A message arriving from a chat channel."""

    session_id: str = ""
    channel: str = ""
    user_id: str = ""
    text: str = ""
    message_id: str = ""
    reply_to: str = ""
    account_id: str = ""
    retry_count: int = 0
    attachments: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class OutboundEvent(Event):
    """A message being sent back to a chat channel."""

    session_id: str = ""
    channel: str = ""
    target: str = ""
    text: str = ""
    reply_to: str = ""
    error: str | None = None


@dataclass
class AgentEvent(Event):
    """Streaming event from the vtx agent run, forwarded to WebSocket clients."""

    session_id: str = ""
    stream: str = ""
    run_id: str = ""
    seq: int = 0
    data: dict[str, Any] = field(default_factory=dict)


Handler = Callable[[Any], Awaitable[None]]


class EventBus:
    """Async pub/sub bus for vtx_claw channel-level events.

    Separate from vtx's extension EventBus — this one routes channel
    messages, lifecycle events, and WebSocket broadcasts within the
    gateway process.
    """

    def __init__(self) -> None:
        self._handlers: dict[type, list[Handler]] = {}
        self._queue: asyncio.Queue[Event] = asyncio.Queue()
        self._running = False
        self._task: asyncio.Task | None = None

    def subscribe(self, event_class: type[E], handler: Callable[[E], Awaitable[None]]) -> None:
        if event_class not in self._handlers:
            self._handlers[event_class] = []
        self._handlers[event_class].append(handler)

    def unsubscribe(self, handler: Handler) -> None:
        for event_type in list(self._handlers):
            self._handlers[event_type] = [
                h for h in self._handlers[event_type] if h is not handler
            ]

    async def publish(self, event: Event) -> None:
        await self._queue.put(event)

    def publish_sync(self, event: Event) -> None:
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("EventBus queue full, dropping event %s", event.type)

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("EventBus started")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        logger.info("EventBus stopped")

    async def _loop(self) -> None:
        while self._running:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except TimeoutError:
                continue
            try:
                await self._dispatch(event)
            except Exception:
                logger.exception("Error dispatching event %s", event.type)
            finally:
                self._queue.task_done()

    async def _dispatch(self, event: Event) -> None:
        for event_type in type(event).__mro__:
            for handler in self._handlers.get(event_type, []):
                try:
                    await handler(event)
                except Exception:
                    logger.exception("Handler error for %s", event.type)


__all__ = [
    "AgentEndEvent",
    "AgentEvent",
    "AgentStartEvent",
    "ErrorEvent",
    "Event",
    "EventBus",
    "InboundEvent",
    "OutboundEvent",
    "TextDeltaEvent",
    "TextEndEvent",
    "ThinkingDeltaEvent",
    "ThinkingEndEvent",
    "ThinkingStartEvent",
    "ToolEndEvent",
    "ToolResultEvent",
    "ToolStartEvent",
    "TurnEndEvent",
    "TurnStartEvent",
]

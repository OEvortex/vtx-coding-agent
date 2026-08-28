"""MessageBus — own async queue, inspired by openjarvis bus.queue."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from .events import InboundMessage, OutboundMessage


class MessageBus:
    def __init__(self) -> None:
        self._inbound: asyncio.Queue[InboundMessage] = asyncio.Queue()
        self._outbound: asyncio.Queue[OutboundMessage] = asyncio.Queue()

    async def publish_inbound(self, msg: InboundMessage) -> None:
        await self._inbound.put(msg)

    async def publish_outbound(self, msg: OutboundMessage) -> None:
        await self._outbound.put(msg)

    async def consume_inbound(self) -> InboundMessage:
        return await self._inbound.get()

    async def consume_outbound(self) -> OutboundMessage:
        return await self._outbound.get()

    @property
    def inbound(self) -> asyncio.Queue[InboundMessage]:
        return self._inbound

    @property
    def outbound(self) -> asyncio.Queue[OutboundMessage]:
        return self._outbound

    def inbound_nowait(self) -> InboundMessage | None:
        try:
            return self._inbound.get_nowait()
        except asyncio.QueueEmpty:
            return None

    async def inbound_stream(self) -> AsyncIterator[InboundMessage]:
        while True:
            msg = await self._inbound.get()
            yield msg

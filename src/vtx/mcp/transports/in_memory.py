"""In-memory transport pair for client tests and adapter tests.

Two transports wired to each other: whatever one sends is delivered to the
other's message listeners on the next loop iteration. The microtask hop matters
-- it reproduces the asynchrony of a real transport, so a test that would race
against an in-process delivery cannot pass by accident.
"""

from __future__ import annotations

import asyncio
import copy

from ..jsonrpc import JsonRpcMessage, McpConnectionClosedError
from ..transport import TransportEvents


class InMemoryTransport(TransportEvents):
    def __init__(self) -> None:
        super().__init__()
        self._peer: InMemoryTransport | None = None
        self._started = False
        self._closed = False

    def connect_peer(self, peer: InMemoryTransport) -> None:
        if self._peer is not None:
            raise RuntimeError("In-memory MCP transport already has a peer")
        self._peer = peer

    async def start(self) -> None:
        if self._closed:
            raise McpConnectionClosedError()
        self._started = True

    async def send(self, message: JsonRpcMessage) -> None:
        if not self._started or self._closed:
            raise McpConnectionClosedError()
        peer = self._peer
        if peer is None or not peer._started or peer._closed:
            raise McpConnectionClosedError("In-memory MCP peer is not connected")
        # Deep-copied so a test mutating what it sent cannot retroactively change
        # what the peer observed, the way a real serialization boundary would.
        payload = copy.deepcopy(message)
        asyncio.get_running_loop().call_soon(peer._deliver, payload)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.emit_close()
        peer = self._peer
        if peer is not None:
            await peer.close()

    def emit_error(self, error: BaseException) -> None:
        """Public so a test can simulate a transport-level failure."""
        super().emit_error(error)

    def _deliver(self, message: JsonRpcMessage) -> None:
        if self._closed:
            return
        self.emit_message(message)


def create_in_memory_transport_pair() -> tuple[InMemoryTransport, InMemoryTransport]:
    client = InMemoryTransport()
    server = InMemoryTransport()
    client.connect_peer(server)
    server.connect_peer(client)
    return client, server


__all__ = ["InMemoryTransport", "create_in_memory_transport_pair"]

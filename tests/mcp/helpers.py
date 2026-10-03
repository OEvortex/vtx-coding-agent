"""Shared in-process MCP server for client tests."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

from vtx.mcp.jsonrpc import JSON_RPC_METHOD_NOT_FOUND, McpError, is_jsonrpc_request
from vtx.mcp.transports.in_memory import InMemoryTransport, create_in_memory_transport_pair
from vtx.mcp.types import LATEST_PROTOCOL_VERSION

Handler = Callable[[dict[str, Any]], Any]


class TestServer:
    """A minimal MCP server over the in-memory transport pair.

    Handlers are set per method and replaced mid-test, which is how the tests
    exercise pagination, cancellation, and protocol-version negotiation
    without a subprocess.
    """

    def __init__(self, transport: InMemoryTransport) -> None:
        self.transport = transport
        self.messages: list[dict[str, Any]] = []
        self.handlers: dict[str, Handler] = {}
        transport.on_message(self._on_message)
        self._pending: set[asyncio.Task] = set()

    def set_handler(self, method: str, handler: Handler) -> None:
        self.handlers[method] = handler

    def _on_message(self, message: dict[str, Any]) -> None:
        self.messages.append(message)
        if not is_jsonrpc_request(message):
            return
        task = asyncio.ensure_future(self._respond(message))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _respond(self, message: dict[str, Any]) -> None:
        method = message["method"]
        handler = self.handlers.get(method)
        try:
            if handler is None:
                raise McpError(JSON_RPC_METHOD_NOT_FOUND, f"Method not found: {method}")
            result = handler(message)
            if asyncio.iscoroutine(result):
                result = await result
            await self.transport.send({"jsonrpc": "2.0", "id": message["id"], "result": result})
        except McpError as exc:
            await self.transport.send(
                {
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "error": {"code": exc.code, "message": str(exc.args[0]), "data": exc.data},
                }
            )
        except Exception as exc:
            await self.transport.send(
                {
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "error": {"code": -32603, "message": str(exc)},
                }
            )

    def find(self, method: str) -> list[dict[str, Any]]:
        return [m for m in self.messages if m.get("method") == method]


async def create_server() -> tuple[InMemoryTransport, TestServer]:
    client_transport, server_transport = create_in_memory_transport_pair()
    server = TestServer(server_transport)
    await server_transport.start()
    server.set_handler(
        "initialize",
        lambda _m: {
            "protocolVersion": LATEST_PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": True}},
            "serverInfo": {"name": "test-server", "version": "1.0.0"},
            "instructions": "Use test tools.",
        },
    )
    return client_transport, server


async def connect(**options: Any):
    client_transport, server = await create_server()
    from vtx.mcp import McpClient, McpClientOptions

    client = McpClient(McpClientOptions(name="test-client", version="2.0.0", **options))
    await client.connect(client_transport)
    return client, server


__all__ = ["Handler", "TestServer", "connect", "create_server", "json"]

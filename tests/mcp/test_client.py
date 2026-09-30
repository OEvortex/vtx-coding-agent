import asyncio

import pytest

from tests.mcp.helpers import connect, create_server
from vtx.mcp import (
    McpAbortError,
    McpClient,
    McpClientOptions,
    McpConnectionClosedError,
    McpRequestOptions,
    McpTimeoutError,
)
from vtx.mcp.jsonrpc import McpError


def _opts(**kwargs) -> McpRequestOptions:
    return McpRequestOptions(**kwargs)


async def _never_responds(_message):
    await asyncio.Event().wait()


def _send_progress(server, token, step: int = 1):
    asyncio.ensure_future(
        server.transport.send(
            {
                "jsonrpc": "2.0",
                "method": "notifications/progress",
                "params": {"progressToken": token, "progress": step, "total": 10},
            }
        )
    )


async def _settle() -> None:
    """Let queued cross-transport deliveries run.

    The in-memory pair hands off with ``loop.call_soon``, the analogue of the
    reference's ``queueMicrotask``, so asserting on what the *other* side
    received needs a few loop turns rather than none.
    """
    for _ in range(10):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_initializes_before_exposing_server_info():
    client, server = await connect()

    assert client.connection_state == "connected"
    assert client.protocol_version == "2025-11-25"
    assert client.server_info == {"name": "test-server", "version": "1.0.0"}
    assert client.server_capabilities == {"tools": {"listChanged": True}}
    assert client.instructions == "Use test tools."

    await _settle()
    # Exactly the handshake, in order, and nothing else.
    assert server.messages == [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "2.0.0"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
    ]
    await client.close()


@pytest.mark.asyncio
async def test_paginates_tools_and_preserves_definitions():
    client, server = await connect()

    def list_tools(message):
        cursor = (message.get("params") or {}).get("cursor")
        if cursor is None:
            return {
                "tools": [
                    {"name": "search", "description": "Search", "inputSchema": {"type": "object"}}
                ],
                "nextCursor": "page-2",
            }
        return {
            "tools": [
                {
                    "name": "read",
                    "inputSchema": {"type": "object"},
                    "outputSchema": {"type": "object"},
                    "annotations": {"readOnlyHint": True},
                }
            ]
        }

    server.set_handler("tools/list", list_tools)

    assert await client.list_tools() == [
        {"name": "search", "description": "Search", "inputSchema": {"type": "object"}},
        {
            "name": "read",
            "inputSchema": {"type": "object"},
            "outputSchema": {"type": "object"},
            "annotations": {"readOnlyHint": True},
        },
    ]
    await client.close()


@pytest.mark.asyncio
async def test_rejects_a_server_that_repeats_a_cursor():
    client, server = await connect()
    server.set_handler("tools/list", lambda _m: {"tools": [], "nextCursor": "same"})

    # Without the guard this loops forever rather than failing.
    with pytest.raises(RuntimeError, match="duplicate cursor: same"):
        await client.list_tools()
    await client.close()


@pytest.mark.asyncio
async def test_lists_and_reads_resources():
    client, server = await connect()

    def list_resources(message):
        if (message.get("params") or {}).get("cursor") is None:
            return {
                "resources": [{"uri": "file:///a", "name": "a", "mimeType": "text/plain"}],
                "nextCursor": "2",
            }
        return {"resources": [{"uri": "file:///b"}]}

    server.set_handler("resources/list", list_resources)
    server.set_handler(
        "resources/templates/list",
        lambda _m: {
            "resourceTemplates": [{"uriTemplate": "repo://{owner}/{repo}", "name": "repo"}]
        },
    )
    server.set_handler(
        "resources/read", lambda m: {"contents": [{"uri": m["params"]["uri"], "text": "hello"}]}
    )

    # A missing name falls back to the URI.
    assert await client.list_resources() == [
        {"uri": "file:///a", "name": "a", "mimeType": "text/plain"},
        {"uri": "file:///b", "name": "file:///b"},
    ]
    assert await client.list_resource_templates() == [
        {"uriTemplate": "repo://{owner}/{repo}", "name": "repo"}
    ]
    # Single pages pass the cursor through untouched.
    assert await client.list_resources_page() == {
        "resources": [{"uri": "file:///a", "name": "a", "mimeType": "text/plain"}],
        "nextCursor": "2",
    }
    assert await client.list_resources_page("2") == {
        "resources": [{"uri": "file:///b", "name": "file:///b"}]
    }
    assert await client.read_resource("file:///a") == {
        "contents": [{"uri": "file:///a", "text": "hello"}]
    }

    server.set_handler("resources/read", lambda _m: {"contents": [{"uri": "file:///a"}]})
    with pytest.raises(Exception, match="Invalid contents in MCP resources/read result"):
        await client.read_resource("file:///a")

    server.set_handler("resources/list", lambda _m: {"resources": [{"name": "no uri"}]})
    with pytest.raises(Exception, match="Invalid entry in MCP resources/list result"):
        await client.list_resources()
    await client.close()


@pytest.mark.asyncio
async def test_returns_structured_content_and_surfaces_jsonrpc_errors():
    client, server = await connect()

    def call_tool(message):
        params = message["params"]
        if params["name"] == "fail":
            raise McpError(1234, "tool failed", {"retryable": False})
        return {
            "content": [{"type": "text", "text": "ok"}],
            "structuredContent": {"count": (params.get("arguments") or {}).get("count")},
        }

    server.set_handler("tools/call", call_tool)

    assert await client.call_tool("count", {"count": 3}) == {
        "content": [{"type": "text", "text": "ok"}],
        "structuredContent": {"count": 3},
    }

    with pytest.raises(McpError) as excinfo:
        await client.call_tool("fail")
    assert excinfo.value.code == 1234
    assert excinfo.value.data == {"retryable": False}
    await client.close()


@pytest.mark.asyncio
async def test_progress_renews_the_timeout():
    client, server = await connect()

    async def slow_call(message):
        token = message["params"]["_meta"]["progressToken"]
        # Progress every 30ms for 300ms against a 100ms timeout: renewal is what
        # keeps it alive, and the gap between notifications (30ms) is far
        # enough under the timeout (100ms) that scheduler jitter on a loaded
        # machine cannot flip the result.
        for step in range(1, 11):
            asyncio.get_running_loop().call_later(0.03 * step, _send_progress, server, token, step)
        await asyncio.sleep(0.3)
        return {"content": [{"type": "text", "text": "done"}]}

    server.set_handler("tools/call", slow_call)
    seen: list[dict] = []

    result = await client.call_tool("slow", {}, _opts(timeout_ms=100, on_progress=seen.append))

    assert result == {"content": [{"type": "text", "text": "done"}]}
    assert len(seen) == 10
    assert seen[0] == {"progressToken": 2, "progress": 1, "total": 10}
    assert seen[-1] == {"progressToken": 2, "progress": 10, "total": 10}
    await client.close()


@pytest.mark.asyncio
async def test_a_call_that_stops_reporting_progress_still_times_out():
    """The renewal must not be sticky: a server that goes quiet must still die."""
    client, server = await connect()

    async def stops_reporting(message):
        token = message["params"]["_meta"]["progressToken"]
        asyncio.get_running_loop().call_later(0.03, _send_progress, server, token, 1)
        await asyncio.sleep(5)
        return {"content": []}

    server.set_handler("tools/call", stops_reporting)

    with pytest.raises(McpTimeoutError):
        await client.call_tool("slow", {}, _opts(timeout_ms=150, on_progress=lambda _p: None))
    await client.close()


@pytest.mark.asyncio
async def test_cancels_an_aborted_request():
    client, server = await connect()
    server.set_handler("tools/call", _never_responds)

    cancel = asyncio.Event()
    pending = asyncio.ensure_future(client.call_tool("wait", {}, _opts(cancel_event=cancel)))
    await asyncio.sleep(0)
    cancel.set()

    with pytest.raises(McpAbortError):
        await pending

    await _settle()
    assert server.find("notifications/cancelled") == [
        {
            "jsonrpc": "2.0",
            "method": "notifications/cancelled",
            "params": {"requestId": 2, "reason": "Cancelled"},
        }
    ]
    await client.close()


@pytest.mark.asyncio
async def test_times_out_a_request():
    client, server = await connect()
    server.set_handler("tools/call", _never_responds)

    with pytest.raises(McpTimeoutError):
        await client.call_tool("wait", {}, _opts(timeout_ms=10))
    await client.close()


@pytest.mark.asyncio
async def test_reports_transport_errors_without_failing_pending_requests():
    client_transport, server = await create_server()
    client = McpClient(McpClientOptions(name="test-client", version="1.0.0"))
    await client.connect(client_transport)
    errors: list[Exception] = []
    client.on_error(errors.append)

    release: asyncio.Event = asyncio.Event()

    async def wait_for_release(_message):
        await release.wait()
        return {"content": []}

    server.set_handler("tools/call", wait_for_release)
    call = asyncio.ensure_future(client.call_tool("wait"))
    await asyncio.sleep(0)

    # A stray log line on the server's stdout is reported, not fatal.
    client_transport.emit_error(RuntimeError("stray log line"))
    release.set()

    assert await call == {"content": []}
    assert [str(e) for e in errors] == ["stray log line"]
    await client.close()


@pytest.mark.asyncio
async def test_accepts_an_older_protocol_version():
    client_transport, server = await create_server()
    server.set_handler(
        "initialize",
        lambda _m: {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "serverInfo": {"name": "old-server", "version": "0.1.0"},
        },
    )
    client = McpClient(McpClientOptions(name="test-client", version="1.0.0"))
    await client.connect(client_transport)
    assert client.protocol_version == "2024-11-05"
    await client.close()


@pytest.mark.asyncio
async def test_rejects_an_unsupported_protocol_version():
    client_transport, server = await create_server()
    server.set_handler(
        "initialize",
        lambda _m: {
            "protocolVersion": "1999-01-01",
            "capabilities": {},
            "serverInfo": {"name": "ancient-server", "version": "0.1.0"},
        },
    )
    client = McpClient(McpClientOptions(name="test-client", version="1.0.0"))
    with pytest.raises(RuntimeError, match="unsupported protocol version"):
        await client.connect(client_transport)
    assert client.connection_state == "closed"


@pytest.mark.asyncio
async def test_defaults_missing_tool_content_to_an_empty_list():
    client, server = await connect()

    server.set_handler("tools/call", lambda _m: {"structuredContent": {"ok": True}})
    assert await client.call_tool("structured") == {
        "structuredContent": {"ok": True},
        "content": [],
    }

    server.set_handler("tools/call", lambda _m: {"content": "not a list"})
    with pytest.raises(Exception, match="Invalid MCP tools/call result"):
        await client.call_tool("broken")
    await client.close()


@pytest.mark.asyncio
async def test_does_not_cancel_a_timed_out_initialize():
    client_transport, server = await create_server()
    server.set_handler("initialize", _never_responds)
    client = McpClient(
        McpClientOptions(name="test-client", version="1.0.0", request_timeout_ms=10)
    )

    with pytest.raises(McpTimeoutError):
        await client.connect(client_transport)
    await _settle()

    # The spec forbids cancelling `initialize`; the server may still be
    # finishing it, so a cancellation notification would be wrong.
    assert server.find("notifications/cancelled") == []


@pytest.mark.asyncio
async def test_close_listener_fires_once_when_the_transport_drops():
    client, server = await connect()
    closed: list[int] = []
    client.on_close(lambda: closed.append(1))
    server.set_handler("tools/call", _never_responds)

    pending = asyncio.ensure_future(client.call_tool("wait"))
    await asyncio.sleep(0)
    await server.transport.close()

    with pytest.raises(McpConnectionClosedError, match="MCP connection closed"):
        await pending
    assert client.connection_state == "closed"

    await client.close()
    assert len(closed) == 1


@pytest.mark.asyncio
async def test_answers_roots_list_and_dispatches_notifications():
    client_transport, server = await create_server()
    client = McpClient(
        McpClientOptions(
            name="test-client",
            version="1.0.0",
            roots=[{"uri": "file:///workspace", "name": "workspace"}],
        )
    )
    await client.connect(client_transport)

    changed: list[object] = []
    client.on_notification("notifications/tools/list_changed", changed.append)

    await server.transport.send({"jsonrpc": "2.0", "id": "roots", "method": "roots/list"})
    await server.transport.send({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})
    await _settle()

    assert {
        "jsonrpc": "2.0",
        "id": "roots",
        "result": {"roots": [{"uri": "file:///workspace", "name": "workspace"}]},
    } in server.messages
    assert len(changed) == 1
    await client.close()


@pytest.mark.asyncio
async def test_request_before_connect_is_refused():
    client = McpClient(McpClientOptions(name="test-client", version="1.0.0"))
    with pytest.raises(McpConnectionClosedError):
        await client.list_tools()


@pytest.mark.asyncio
async def test_reconnect_after_close_is_refused():
    client, _server = await connect()
    await client.close()
    with pytest.raises(McpConnectionClosedError):
        await client.list_tools()

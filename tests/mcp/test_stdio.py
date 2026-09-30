import asyncio
import os
import re
import sys
import time
from pathlib import Path

import pytest

from vtx.mcp import McpClient, McpClientOptions, StdioTransport, StdioTransportOptions

FIXTURES = Path(__file__).parent / "fixtures"
STDIO_SERVER = FIXTURES / "stdio_server.py"
STUBBORN_SERVER = FIXTURES / "stubborn_server.py"

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="process-group signalling differs on Windows"
)


def _transport(server: Path, **kwargs) -> StdioTransport:
    return StdioTransport(
        StdioTransportOptions(command=sys.executable, args=[str(server)], **kwargs)
    )


async def _connect(transport: StdioTransport) -> McpClient:
    client = McpClient(McpClientOptions(name="stdio-test", version="1.0.0"))
    await client.connect(transport)
    return client


def _wait_for(predicate, timeout: float = 5.0, interval: float = 0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    except OSError:
        return False
    return True


@pytest.mark.asyncio
async def test_connects_to_a_newline_delimited_server_and_captures_stderr():
    chunks: list[str] = []
    transport = _transport(STDIO_SERVER, on_stderr=chunks.append)
    client = await _connect(transport)

    assert client.protocol_version == "2025-06-18"
    assert client.server_info == {"name": "stdio-fixture", "version": "1.0.0"}
    tools = await client.list_tools()
    assert [t["name"] for t in tools] == ["echo"]
    # The declared input schema must survive the round trip intact, since a
    # client builds its params model from exactly this.
    assert tools[0]["inputSchema"]["properties"]["text"]["type"] == "string"
    assert tools[0]["inputSchema"]["required"] == ["text"]
    assert await client.call_tool("echo", {"text": "hello"}) == {
        "content": [{"type": "text", "text": "hello"}]
    }
    assert isinstance(transport.pid, int)

    assert _wait_for(lambda: "stdio fixture ready" in transport.stderr)
    assert "".join(chunks).count("stdio fixture ready") >= 1

    await client.close()
    assert client.connection_state == "closed"


@pytest.mark.asyncio
async def test_surfaces_a_server_side_jsonrpc_error():
    transport = _transport(STDIO_SERVER)
    client = await _connect(transport)

    with pytest.raises(Exception) as excinfo:
        await client.request("nonexistent/method")

    assert excinfo.value.code == -32601
    await client.close()


@pytest.mark.asyncio
async def test_reports_a_command_that_cannot_start():
    transport = StdioTransport(
        StdioTransportOptions(command="definitely-not-a-real-command-xyzzy", args=[])
    )
    client = McpClient(McpClientOptions(name="stdio-test", version="1.0.0"))

    with pytest.raises(RuntimeError, match="Failed to start MCP server"):
        await client.connect(transport)
    assert client.connection_state == "closed"


@pytest.mark.asyncio
async def test_kills_a_server_that_ignores_shutdown_including_its_children():
    transport = _transport(STUBBORN_SERVER, close_timeout_ms=100)
    client = await _connect(transport)

    match = _wait_for(lambda: re.search(r"grandchild (\d+)", transport.stderr))
    assert match is not None, f"grandchild pid never reported: {transport.stderr!r}"
    grandchild = int(match.group(1))
    assert _pid_alive(grandchild)

    started = time.monotonic()
    await client.close()
    elapsed = time.monotonic() - started

    # The grace period plus the SIGTERM window, not the default 2s+ backstop.
    assert elapsed < 5.0
    assert _wait_for(lambda: not _pid_alive(grandchild), timeout=3.0) is not None, (
        "grandchild outlived the transport; the process group was not killed"
    )


@pytest.mark.asyncio
async def test_close_is_idempotent():
    transport = _transport(STDIO_SERVER)
    client = await _connect(transport)

    await client.close()
    await client.close()
    assert client.connection_state == "closed"


@pytest.mark.asyncio
async def test_sending_after_close_is_refused():
    from vtx.mcp import McpConnectionClosedError

    transport = _transport(STDIO_SERVER)
    client = await _connect(transport)
    await client.close()

    with pytest.raises(McpConnectionClosedError):
        await client.list_tools()


@pytest.mark.asyncio
async def test_passes_environment_to_the_child():
    # The fixture echoes nothing, so probe the environment through a tiny
    # server of our own instead of adding output to the shared fixture.
    probe = FIXTURES / "env_probe.py"
    transport = StdioTransport(
        StdioTransportOptions(
            command=sys.executable,
            args=[str(probe)],
            env={"MCP_TEST_VALUE": "present"},
            inherit_env=False,
        )
    )
    client = McpClient(McpClientOptions(name="stdio-test", version="1.0.0"))
    result = await client.connect(transport)

    assert result["serverInfo"]["version"] == "present"
    await client.close()


@pytest.mark.asyncio
async def test_cancellation_stops_a_running_tool_call():
    from vtx.mcp import McpAbortError, McpRequestOptions

    slow = FIXTURES / "slow_server.py"
    transport = _transport(slow)
    client = await _connect(transport)

    cancel = asyncio.Event()
    pending = asyncio.ensure_future(
        client.call_tool("slow", {}, McpRequestOptions(cancel_event=cancel, timeout_ms=10_000))
    )
    await asyncio.sleep(0.2)
    cancel.set()

    with pytest.raises(McpAbortError):
        await pending
    await client.close()


@pytest.mark.asyncio
async def test_timeout_kills_the_child_process():
    from vtx.mcp import McpTimeoutError, McpRequestOptions

    slow = FIXTURES / "slow_server.py"
    transport = _transport(slow)
    client = await _connect(transport)
    pid = transport.pid

    with pytest.raises(McpTimeoutError):
        await client.call_tool("slow", {}, McpRequestOptions(timeout_ms=150))

    await client.close()
    # The server never exits on its own, so this only passes if close() took
    # the process down rather than leaking it.
    assert _wait_for(lambda: not _pid_alive(pid), timeout=3.0) is not None

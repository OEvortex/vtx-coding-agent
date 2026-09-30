import asyncio
import json
import sys
from pathlib import Path

import pytest

from vtx.mcp.config import LoadedMcpConfig
from vtx.mcp.manager import McpManager, cwd_to_uri

FIXTURES = Path(__file__).parent / "fixtures"
STDIO_SERVER = FIXTURES / "stdio_server.py"
SLOW_SERVER = FIXTURES / "slow_server.py"


def _config(*servers: dict) -> LoadedMcpConfig:
    from vtx.mcp.config import validate_mcp_server_config

    configs = []
    for entry in servers:
        parsed, error = validate_mcp_server_config(entry["name"], entry["spec"])
        assert error is None, error
        assert parsed is not None
        parsed.name = entry["name"]
        configs.append(parsed)
    return LoadedMcpConfig(servers=configs)


def _stdio(name: str = "echo", script: Path = STDIO_SERVER, **extra) -> dict:
    return {
        "name": name,
        "spec": {
            "command": sys.executable,
            "args": [str(script)],
            **extra,
        },
    }


@pytest.mark.asyncio
async def test_connects_to_a_stdio_server_and_exposes_its_tools(tmp_path):
    manager = McpManager(cwd=str(tmp_path), config=_config(_stdio()))
    try:
        tools = await manager.connect_all()

        assert [t.name for t in tools] == ["mcp__echo__echo"]
        assert tools[0].description  # has a description from the server or the fallback
        status = manager.statuses()[0]
        assert status.state == "connected"
        assert status.tool_count == 1
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_a_connected_tool_actually_calls_the_server(tmp_path):
    manager = McpManager(cwd=str(tmp_path), config=_config(_stdio()))
    try:
        tools = await manager.connect_all()
        tool = tools[0]
        result = await tool.execute(tool.params(text="hello"))
        assert result.success is True
        assert result.result == "hello"
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_close_stops_the_server_process(tmp_path):
    manager = McpManager(cwd=str(tmp_path), config=_config(_stdio()))
    await manager.connect_all()
    connection = manager.get("echo")
    assert connection is not None
    pid = connection.client._transport.pid  # ty: ignore[possibly-unbound-attribute]

    await manager.close()
    await asyncio.sleep(0.1)

    import os

    with pytest.raises(OSError):
        os.kill(pid, 0)


@pytest.mark.asyncio
async def test_a_disabled_server_is_never_started(tmp_path):
    manager = McpManager(
        cwd=str(tmp_path), config=_config(_stdio(**{"enabled": False}))
    )
    try:
        tools = await manager.connect_all()
        assert tools == []
        assert manager.statuses()[0].state == "disabled"
        assert manager.get("echo").client is None
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_a_server_that_cannot_start_is_reported_not_raised(tmp_path):
    manager = McpManager(
        cwd=str(tmp_path),
        config=_config({"name": "bad", "spec": {"command": "definitely-not-a-command-xyzzy"}}),
    )
    try:
        tools = await manager.connect_all()

        assert tools == []
        status = manager.statuses()[0]
        assert status.state == "failed"
        assert "definitely-not-a-command-xyzzy" in status.describe()
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_one_broken_server_does_not_block_a_working_one(tmp_path):
    manager = McpManager(
        cwd=str(tmp_path),
        config=_config(
            {"name": "bad", "spec": {"command": "definitely-not-a-command-xyzzy"}},
            _stdio("good"),
        ),
    )
    try:
        tools = await manager.connect_all()

        assert [t.name for t in tools] == ["mcp__good__echo"]
        states = {s.name: s.state for s in manager.statuses()}
        assert states == {"bad": "failed", "good": "connected"}
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_tool_names_never_collide_across_servers(tmp_path):
    manager = McpManager(cwd=str(tmp_path), config=_config(_stdio("a"), _stdio("b")))
    try:
        tools = await manager.connect_all()
        names = [t.name for t in tools]
        assert sorted(names) == ["mcp__a__echo", "mcp__b__echo"]
        assert len(set(names)) == len(names)
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_mcp_tools_never_shadow_builtin_tools(tmp_path):
    # A server named to collide with a built-in must not take its name.
    from vtx.coding_agent.tools import all_tools

    builtin = next(t.name for t in all_tools if t.name == "web")
    manager = McpManager(cwd=str(tmp_path), config=_config(_stdio("web")))
    try:
        tools = await manager.connect_all()
        assert builtin not in [t.name for t in tools]
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_roots_are_advertised_to_the_server(tmp_path):
    manager = McpManager(cwd=str(tmp_path), config=_config(_stdio()))
    try:
        await manager.connect_all()
        connection = manager.get("echo")
        assert connection is not None and connection.client is not None
        assert manager.roots[0]["uri"] == cwd_to_uri(str(tmp_path))
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_startup_wait_does_not_raise_when_a_server_is_slow(tmp_path):
    """A server still connecting at the deadline must not hold up the session."""
    manager = McpManager(
        cwd=str(tmp_path),
        config=_config(_stdio("slow", SLOW_SERVER)),
        startup_wait_seconds=0.05,
    )
    try:
        tools = await manager.connect_all()
        assert tools == []
        status = manager.statuses()[0]
        assert status.state in ("connecting", "connected")
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_close_is_idempotent(tmp_path):
    manager = McpManager(cwd=str(tmp_path), config=_config(_stdio()))
    await manager.connect_all()
    await manager.close()
    await manager.close()


@pytest.mark.asyncio
async def test_an_empty_config_yields_nothing(tmp_path):
    manager = McpManager(cwd=str(tmp_path), config=LoadedMcpConfig())
    try:
        assert await manager.connect_all() == []
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_config_errors_are_surfaced_on_the_manager(tmp_path):
    (tmp_path / "mcp.json").write_text(
        json.dumps({"mcpServers": {"broken": {"url": "not-a-url"}}}), encoding="utf-8"
    )
    manager = McpManager(cwd=str(tmp_path), config_dir=tmp_path)
    try:
        assert manager.errors
        assert "not-a-url" in manager.errors[0] or "http" in manager.errors[0]
        assert await manager.connect_all() == []
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_reload_picks_up_a_new_server(tmp_path, monkeypatch):
    manager = McpManager(cwd=str(tmp_path), config=LoadedMcpConfig(), config_dir=tmp_path)
    try:
        assert await manager.connect_all() == []

        (tmp_path / "mcp.json").write_text(
            json.dumps(
                {"mcpServers": {"echo": {"command": sys.executable, "args": [str(STDIO_SERVER)]}}}
            ),
            encoding="utf-8",
        )
        tools = await manager.reload()
        assert [t.name for t in tools] == ["mcp__echo__echo"]
    finally:
        await manager.close()


def test_cwd_to_uri_quotes_spaces():
    assert cwd_to_uri("/tmp/a b") == "file:///tmp/a%20b"


@pytest.mark.asyncio
async def test_status_describe_reads_as_prose(tmp_path):
    from vtx.mcp.manager import McpServerStatus

    assert McpServerStatus("a", state="connected", tool_count=1).describe() == "connected (1 tool)"
    assert McpServerStatus("a", state="connected", tool_count=3).describe() == "connected (3 tools)"
    assert McpServerStatus("a", state="needs-auth").describe() == "needs sign-in"
    assert McpServerStatus("a", state="disabled").describe() == "disabled"
    assert "boom" in McpServerStatus("a", state="failed", error="boom").describe()

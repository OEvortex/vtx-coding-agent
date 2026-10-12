"""MCP through the runtime, which is where the pieces meet.

``tests/mcp`` covers the client, the manager, and the flows in isolation. What
is left is the composition: the runtime building a manager that reads both
config files, the tool set landing where the agent can see it, and a reload
taking effect on a live session rather than at the next restart.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from vtx.agent.runtime import ConversationRuntime
from vtx.plugins.bus import EventBus

FIXTURES = Path(__file__).parent / "mcp" / "fixtures"
STDIO_SERVER = FIXTURES / "stdio_server.py"

pytestmark = pytest.mark.asyncio


def _runtime(cwd: Path) -> ConversationRuntime:
    return ConversationRuntime(cwd=str(cwd), model="gpt-test", tools=[], extensions=EventBus())


def _write_mcp(directory: Path, servers: dict) -> None:
    path = directory / ".vtx" / "mcp.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")


def _echo_server(name: str = "echo") -> dict:
    return {"command": sys.executable, "args": [str(STDIO_SERVER)]}


# ---- a project's servers start on their own -------------------------------


async def test_a_project_server_starts_on_connect(tmp_path):
    """No grant, no prompt: the file is read and the server is started."""
    _write_mcp(tmp_path, {"echo": _echo_server()})

    runtime = _runtime(tmp_path)
    try:
        tools = await runtime.connect_mcp()
        assert "mcp__echo__echo" in [t.name for t in tools]
    finally:
        await runtime.close()


async def test_disabling_a_server_keeps_it_from_running(tmp_path):
    """The only remaining control over a project server is `enabled`, which is
    what `exposure` and `enabled` were always for once the gate is gone."""
    _write_mcp(tmp_path, {"echo": {**_echo_server(), "enabled": False}})

    runtime = _runtime(tmp_path)
    try:
        assert await runtime.connect_mcp() == []
    finally:
        await runtime.close()


# ---- tools land in the agent surface --------------------------------------


async def test_mcp_tools_reach_the_live_tool_set(tmp_path):
    """The whole point: they are ordinary tools from the agent's side."""
    runtime = _runtime(tmp_path)
    try:
        manager = runtime.ensure_mcp_manager()
        from vtx.mcp.config import LoadedMcpConfig, validate_mcp_server_config

        config, error = validate_mcp_server_config("echo", _echo_server())
        assert error is None, error
        manager.config = LoadedMcpConfig(servers=[config])
        manager._build_servers(manager.config)

        tools = await runtime.connect_mcp()
        names = [t.name for t in tools]
        assert "mcp__echo__echo" in names
        assert "read_mcp_resource" in names

        runtime.sync_mcp_tools(tools)
        assert "mcp__echo__echo" in [t.name for t in runtime.tools]
    finally:
        await runtime.close()


async def test_closing_the_runtime_closes_every_server(tmp_path):
    """A leaked stdio server is a leaked process holding a pipe."""
    runtime = _runtime(tmp_path)
    manager = runtime.ensure_mcp_manager()
    from vtx.mcp.config import LoadedMcpConfig, validate_mcp_server_config

    config, error = validate_mcp_server_config("echo", _echo_server())
    assert error is None, error
    manager.config = LoadedMcpConfig(servers=[config])
    manager._build_servers(manager.config)
    await runtime.connect_mcp()

    connection = manager.get("echo")
    assert connection is not None and connection.client is not None
    await runtime.close()
    assert connection.client is None

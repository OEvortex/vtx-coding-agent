"""MCP through the runtime, which is where the pieces meet.

``tests/mcp`` covers the client, the manager, and the flows in isolation. What
is left is the composition: the runtime building a manager that honours project
trust, the tool set landing where the agent can see it, and a trust change
taking effect on a live session rather than at the next restart.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from vtx.agent.extensions import EventBus
from vtx.agent.runtime import ConversationRuntime

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


# ---- project trust through the runtime ------------------------------------


async def test_a_project_server_does_not_start_without_trust(tmp_path, monkeypatch):
    """The security property, at the layer that actually builds the manager."""
    _write_mcp(tmp_path, {"echo": _echo_server()})
    monkeypatch.setattr(
        "vtx.mcp.trust.ProjectTrustStore.path", property(lambda self: tmp_path / "trust.json")
    )

    runtime = _runtime(tmp_path)
    try:
        assert await runtime.connect_mcp() == []
        assert runtime.project_trusted is False
    finally:
        await runtime.close()


async def test_trusting_a_project_makes_its_servers_start(tmp_path, monkeypatch):
    _write_mcp(tmp_path, {"echo": _echo_server()})
    monkeypatch.setattr(
        "vtx.mcp.trust.ProjectTrustStore.path", property(lambda self: tmp_path / "trust.json")
    )

    runtime = _runtime(tmp_path)
    try:
        assert await runtime.connect_mcp() == []

        # A granted trust is useless if it waits for a restart, so the runtime
        # rebuilds the servers as part of applying it.
        tools = await runtime.apply_project_trust(True)
        assert runtime.project_trusted is True
        assert "mcp__echo__echo" in [t.name for t in tools]
    finally:
        await runtime.close()


async def test_revoking_trust_removes_the_servers_tools(tmp_path, monkeypatch):
    _write_mcp(tmp_path, {"echo": _echo_server()})
    monkeypatch.setattr(
        "vtx.mcp.trust.ProjectTrustStore.path", property(lambda self: tmp_path / "trust.json")
    )

    runtime = _runtime(tmp_path)
    try:
        trusted = await runtime.apply_project_trust(True)
        assert [t.name for t in trusted if t.name.startswith("mcp__")] == ["mcp__echo__echo"]

        revoked = await runtime.apply_project_trust(False)
        assert not [t for t in revoked if t.name.startswith("mcp__")]
        assert runtime.project_trusted is False
    finally:
        await runtime.close()


async def test_trust_granted_before_any_mcp_work_still_takes_effect(tmp_path, monkeypatch):
    """The result must not depend on whether connect_mcp ran first.

    A grant that reports success while starting nothing is worse than one that
    fails, so this goes straight to apply_project_trust with no prior connect.
    """
    _write_mcp(tmp_path, {"echo": _echo_server()})
    monkeypatch.setattr(
        "vtx.mcp.trust.ProjectTrustStore.path", property(lambda self: tmp_path / "trust.json")
    )
    runtime = _runtime(tmp_path)
    try:
        tools = await runtime.apply_project_trust(True)
        assert [t.name for t in tools if t.name.startswith("mcp__")] == ["mcp__echo__echo"]
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

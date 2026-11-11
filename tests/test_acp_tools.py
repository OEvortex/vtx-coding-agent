"""ACP sessions run with the composition's tools, not a bare harness.

The bug this pins: ``vtx acp`` never mounted the plugin composition, so the
coding agent's tools -- ``read``, ``edit``, ``write``, ``bash``, ``skill``,
``todo`` -- were never registered. The model saw only the three harness-native
tools and could not touch the workspace at all. The row list is authoritative,
so the fix is to mount it and assemble from ``ctx.tools``.
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from typing import ClassVar

import pytest

from vtx.ai.providers.mock import MockProvider
from vtx.coding_agent.acp import AcpServer, _session_tools
from vtx.plugins import boot, reset

CODING_AGENT_TOOLS = {"read", "apply_patch", "bash", "skill", "todo"}


class RecordingProvider(MockProvider):
    """MockProvider that records the tool definitions the agent declares."""

    instances: ClassVar[list[RecordingProvider]] = []

    def __init__(self, config=None, scenario="simple_text"):
        super().__init__(config, scenario)
        self.tool_names: list[str] = []
        RecordingProvider.instances.append(self)

    async def _stream_impl(self, messages, **kwargs):
        self.tool_names = [getattr(t, "name", "") for t in (kwargs.get("tools") or [])]
        return await super()._stream_impl(messages, **kwargs)


@pytest.fixture(autouse=True)
def clean_composition():
    """Start and end each test with no host mounted.

    Tool registration is global, so a host left behind by one test would make
    the next one pass for the wrong reason.
    """
    reset()
    yield
    reset()


def test_session_tools_fall_back_without_a_composition():
    """A bare process still gets the product's default list."""
    from vtx.coding_agent.tools import DEFAULT_TOOLS, get_tools_with_extensions

    assert [t.name for t in _session_tools()] == [
        t.name for t in get_tools_with_extensions(DEFAULT_TOOLS)
    ]


def test_session_tools_come_from_the_mounted_composition(tmp_path):
    host = boot("headless", cwd=str(tmp_path))
    try:
        names = {tool.name for tool in _session_tools()}
    finally:
        host.dispose()

    # The coding agent's tools are the ones that were missing.
    assert names >= CODING_AGENT_TOOLS
    # Both ends of the row list survive: harness tools and the product's tools.
    assert names >= {"web", "ask_user", "delegate_subagent"}


def test_a_profile_that_removes_a_tool_row_removes_the_tool(tmp_path):
    """The composition, not ``DEFAULT_TOOLS``, decides what a session has.

    Assembling from the fixed list here is exactly how the missing tools were
    masked: a hardcoded list cannot express a row list that was edited.
    """
    host = boot("headless", cwd=str(tmp_path))
    try:
        host.base.tools.set_defaults(["read"])
        names = {tool.name for tool in _session_tools()}
    finally:
        host.dispose()

    assert names == {"read"}


def test_run_acp_mounts_the_composition_before_serving(monkeypatch):
    """``vtx acp`` must boot: without it the registry has no coding tools."""
    from vtx.coding_agent import acp

    calls: dict[str, object] = {}

    def fake_boot(profile=None, **kwargs):
        calls["profile"] = profile
        calls["cwd"] = kwargs.get("cwd")
        calls["extension_paths"] = kwargs.get("extension_paths")
        return SimpleNamespace(dispose=lambda: calls.update(disposed=True))

    async def fake_serve(self):
        calls["served"] = True

    monkeypatch.setattr("vtx.plugins.boot", fake_boot)
    monkeypatch.setattr(AcpServer, "_serve", fake_serve)

    acp.run_acp()

    assert calls["profile"] == (os.environ.get("VTX_PROFILE") or "headless")
    assert calls["cwd"] == os.getcwd()
    assert calls["extension_paths"] is not None
    assert calls["served"] is True
    assert calls.get("disposed") is True


@pytest.mark.asyncio
async def test_acp_prompt_offers_the_coding_agent_tools(monkeypatch, tmp_path):
    """End to end: a prompt over ACP declares the workspace tools to the model."""
    monkeypatch.setattr(
        "vtx.agent.runtime.create_provider", lambda api_type, config: RecordingProvider(config)
    )
    RecordingProvider.instances.clear()

    host = boot("headless", cwd=str(tmp_path))
    start_cwd = os.getcwd()
    server = AcpServer()
    try:
        session_id = server.session_new({"cwd": str(tmp_path)})["sessionId"]
        result = await server.session_prompt(
            {"sessionId": session_id, "prompt": [{"type": "text", "text": "hi"}]}
        )
    finally:
        os.chdir(start_cwd)
        # The agent's own child tasks (MCP connect, command publish) must not
        # outlive the test, or the suite hangs instead of failing.
        for sess in server._sessions.values():
            for task in (sess._cmd_task, sess._mcp_task, sess._warm_task):
                if isinstance(task, asyncio.Task) and not task.done():
                    task.cancel()
        await asyncio.sleep(0)
        host.dispose()

    assert result == {"stopReason": "end_turn"}
    assert RecordingProvider.instances, "provider was never called"
    declared = set(RecordingProvider.instances[0].tool_names)
    assert declared >= CODING_AGENT_TOOLS, f"missing tools: {CODING_AGENT_TOOLS - declared}"

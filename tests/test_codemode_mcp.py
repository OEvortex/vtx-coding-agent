"""The whole path: an MCP server's tools, reached from inside a codemode script.

This is the test that would have failed before any of the work. An MCP tool was
reachable by the model as an ordinary tool call and invisible to every script,
which is backwards -- code mode is the mechanism that makes a large MCP tool set
affordable at all, so the tools a server publishes should arrive *there*.

Everything below runs against a real server process over a real stdio
transport. A unit test with a stubbed ``McpTool`` would not catch the actual
failure, which is a wiring mistake: the tool exists, the sandbox is built, and
the two are never connected.
"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from vtx.ai.agent.codemode import CodemodeSandbox, adapt_tools
from vtx.ai.agent.extensions import EventBus
from vtx.ai.agent.runtime import ConversationRuntime
from vtx.ai.agent.tools import get_all_tools
from vtx.mcp.config import LoadedMcpConfig, validate_mcp_server_config
from vtx.mcp.manager import McpManager

FIXTURES = Path(__file__).parent / "mcp" / "fixtures"
STDIO_SERVER = FIXTURES / "stdio_server.py"

pytestmark = pytest.mark.asyncio


def _config(**server_extras: dict) -> LoadedMcpConfig:
    """One stdio server pointing at the fixture, plus any extra config keys.

    ``server_extras`` is how the exposure tests configure a server: the config is
    validated through the real parser, so a typo in an exposure key fails here
    rather than silently falling back to the default.
    """
    parsed, error = validate_mcp_server_config(
        "echo", {"command": sys.executable, "args": [str(STDIO_SERVER)], **server_extras}
    )
    assert error is None, error
    assert parsed is not None
    return LoadedMcpConfig(servers=[parsed])


@asynccontextmanager
async def _connected(tmp_path: Path, **server_extras: dict):
    """A live manager, for as long as the test needs its tools callable.

    Not a plain ``await``: the tool objects hold a reference to the connection,
    so closing the manager before a script calls one fails with "server is shut
    down" rather than anything to do with code mode.
    """
    manager = McpManager(cwd=str(tmp_path), config=_config(**server_extras))
    try:
        yield await manager.connect_all()
    finally:
        await manager.close()


def _server_tools(tools) -> list:
    return [t for t in tools if t.name.startswith("mcp__")]


async def test_an_mcp_tool_is_callable_from_inside_a_script(tmp_path):
    async with _connected(tmp_path) as tools:
        tools = _server_tools(tools)
        assert tools, "the fixture server should publish one tool"

        sandbox = CodemodeSandbox(tools=adapt_tools(tools))
        result = await sandbox.execute(
            "r = await tools.mcp__echo__echo(text='hello')\nreturn r['content'][0]['text']"
        )
    assert result.ok, result.diagnostic
    assert result.value == "hello"


async def test_a_script_gets_the_structured_result_not_the_flattened_text(tmp_path):
    # The model-facing string is rendered and truncated; a script gets the whole
    # CallToolResult. That difference is the feature: a script can index into it
    # and branch on it, which a truncated string cannot support.
    async with _connected(tmp_path) as tools:
        sandbox = CodemodeSandbox(tools=adapt_tools(_server_tools(tools)))
        result = await sandbox.execute(
            "r = await tools.mcp__echo__echo(text='x')\nreturn sorted(r)"
        )
    assert result.ok
    # The server's result, verbatim: no truncation, no flattening, and no
    # `_meta` -- that is server plumbing that can carry cursors and credentials,
    # and of no use to a script.
    assert result.value == ["content"]


async def test_the_catalog_groups_an_mcp_servers_tools_under_its_namespace(tmp_path):
    async with _connected(tmp_path) as tools:
        sandbox = CodemodeSandbox(
            tools=adapt_tools(_server_tools(tools)), catalog_budget_tokens=2000
        )
        instructions = sandbox.instructions()
    assert "# mcp__echo (1 tool)" in instructions
    # The declaration names the value's type, so the model knows to index into
    # it rather than treat it as a string.
    assert "CallToolResult" in instructions


async def test_a_hidden_tool_is_callable_by_nobody(tmp_path):
    # `hidden` has to mean unreachable, not merely unlisted. A model that found
    # it would be told about a capability the operator configured away.
    async with _connected(tmp_path, exposure="hidden") as tools:
        tools = _server_tools(tools)
        assert tools
        assert all(t.exposure == "hidden" for t in tools)

    from vtx.ai.agent.tools import ToolSearchTool

    assert ToolSearchTool().searchable() == []


async def test_the_harness_tool_reaches_mcp_tools_through_the_runtime(tmp_path):
    # The end-to-end claim: a session with a connected server, using the
    # registered `codemode` tool, can call that server's tools from a script.
    from vtx.ai.agent.tools.codemode import CodemodeParams

    runtime = ConversationRuntime(
        cwd=str(tmp_path), model="gpt-test", tools=[], extensions=EventBus()
    )
    try:
        manager = runtime.ensure_mcp_manager()
        parsed, error = validate_mcp_server_config(
            "echo", {"command": sys.executable, "args": [str(STDIO_SERVER)]}
        )
        assert error is None, error
        manager.config = LoadedMcpConfig(servers=[parsed])
        manager._build_servers(manager.config)
        await runtime.connect_mcp()
        runtime.sync_mcp_tools(await manager.connect_all())

        mcp_names = [t.name for t in runtime.tools if t.name.startswith("mcp__")]
        assert mcp_names, "the server's tools should be in the session tool set"

        codemode = get_all_tools()["codemode"]
        sandbox_names = {t.name for t in codemode.session_tools()}
        assert set(mcp_names) <= sandbox_names, (
            "an MCP tool the model can call must also be callable from a script"
        )

        result = await codemode.execute(
            CodemodeParams(code=f"r = await tools.{mcp_names[0]}(text='hi')\nreturn r")
        )
        assert result.success, result.result
    finally:
        await runtime.close()


async def test_a_codemode_exposed_tool_is_not_declared_to_the_model(tmp_path, monkeypatch):
    """The taxonomy's whole purpose, asserted end to end.

    Everything else about the wiring can be right while this is wrong: the tool
    reaches a script, appears in the catalog, and is findable -- and the model
    is still sent two hundred tool definitions per turn, which is the cost the
    exposure setting exists to remove.
    """
    from vtx.ai.agent.tools import get_tool_definitions

    runtime = ConversationRuntime(
        cwd=str(tmp_path), model="gpt-test", tools=[], extensions=EventBus()
    )
    try:
        manager = runtime.ensure_mcp_manager()
        parsed, error = validate_mcp_server_config(
            "echo",
            {"command": sys.executable, "args": [str(STDIO_SERVER)], "exposure": "codemode"},
        )
        assert error is None, error
        manager.config = LoadedMcpConfig(servers=[parsed])
        manager._build_servers(manager.config)
        runtime.sync_mcp_tools(await manager.connect_all())

        declared = {d.name for d in get_tool_definitions(runtime.declared_tools())}
        assert "mcp__echo__echo" not in declared
        # ...and still in the pool, so a script and a search can both reach it.
        assert "mcp__echo__echo" in {t.name for t in runtime.tools}
    finally:
        await runtime.close()


async def test_a_direct_exposed_tool_is_declared_and_still_callable(tmp_path, monkeypatch):
    """Declared directly *and* composable is not a conflict.

    A model needing six of a server's tools in one turn should not have to pay
    six, so `direct` does not withdraw a tool from the sandbox. It is only left
    out of the codemode catalog, since the model already has it in front of it.
    """
    from vtx.ai.agent.tools import get_tool_definitions

    runtime = ConversationRuntime(
        cwd=str(tmp_path), model="gpt-test", tools=[], extensions=EventBus()
    )
    try:
        manager = runtime.ensure_mcp_manager()
        parsed, error = validate_mcp_server_config(
            "echo", {"command": sys.executable, "args": [str(STDIO_SERVER)], "exposure": "direct"}
        )
        assert error is None, error
        manager.config = LoadedMcpConfig(servers=[parsed])
        manager._build_servers(manager.config)
        runtime.sync_mcp_tools(await manager.connect_all())

        declared = {d.name for d in get_tool_definitions(runtime.declared_tools())}
        assert "mcp__echo__echo" in declared

        codemode = get_all_tools()["codemode"]
        assert "mcp__echo__echo" in {t.name for t in codemode.session_tools()}
        # Not re-listed: the model already has it.
        assert "mcp__echo__echo" not in codemode.build_description()
    finally:
        await runtime.close()


async def test_a_per_tool_override_beats_the_server_exposure(tmp_path, monkeypatch):
    """The documented shape, through the real parser and a real server.

    `{"*": "direct", "echo": "codemode"}` has to resolve as written: the exact
    name beats the pattern, so the one tool the operator wants composed arrives
    through a script rather than as its own tool definition.
    """
    from vtx.ai.agent.tools import get_tool_definitions

    runtime = ConversationRuntime(
        cwd=str(tmp_path), model="gpt-test", tools=[], extensions=EventBus()
    )
    try:
        manager = runtime.ensure_mcp_manager()
        parsed, error = validate_mcp_server_config(
            "echo",
            {
                "command": sys.executable,
                "args": [str(STDIO_SERVER)],
                "exposure": "direct",
                "tool_exposure": {"*": "direct", "echo": "codemode"},
            },
        )
        assert error is None, error
        manager.config = LoadedMcpConfig(servers=[parsed])
        manager._build_servers(manager.config)
        tools = await manager.connect_all()
        runtime.sync_mcp_tools(tools)

        assert [t.exposure for t in tools if t.name == "mcp__echo__echo"] == ["codemode"]
        declared = {d.name for d in get_tool_definitions(runtime.declared_tools())}
        assert "mcp__echo__echo" not in declared
    finally:
        await runtime.close()


async def test_a_hidden_tool_leaves_the_session_tool_list_entirely(tmp_path, monkeypatch):
    """Unreachable, not merely unlisted.

    Every consumer already excludes a hidden tool, so a version that kept it in
    the list and filtered on the way out would behave identically today. It is
    dropped at the source so the next consumer cannot reach one by forgetting.
    """
    runtime = ConversationRuntime(
        cwd=str(tmp_path), model="gpt-test", tools=[], extensions=EventBus()
    )
    try:
        manager = runtime.ensure_mcp_manager()
        parsed, error = validate_mcp_server_config(
            "echo", {"command": sys.executable, "args": [str(STDIO_SERVER)], "exposure": "hidden"}
        )
        assert error is None, error
        manager.config = LoadedMcpConfig(servers=[parsed])
        manager._build_servers(manager.config)
        tools = await manager.connect_all()
        runtime.sync_mcp_tools(tools)

        assert "mcp__echo__echo" not in {t.name for t in runtime.tools}
        assert "mcp__echo__echo" not in {t.name for t in runtime.declared_tools()}
        assert "mcp__echo__echo" not in {
            t.name for t in get_all_tools()["codemode"].session_tools()
        }
    finally:
        await runtime.close()


async def test_a_becoming_hidden_is_cleaned_out_of_a_live_session(tmp_path, monkeypatch):
    """The `list_changed` path, which is the one that actually runs.

    A server that drops or narrows a tool while connected must not leave the old
    one behind, or a tool the operator just hid stays callable for the rest of
    the session.
    """
    runtime = ConversationRuntime(
        cwd=str(tmp_path), model="gpt-test", tools=[], extensions=EventBus()
    )
    try:
        manager = runtime.ensure_mcp_manager()
        parsed, error = validate_mcp_server_config(
            "echo",
            {"command": sys.executable, "args": [str(STDIO_SERVER)], "exposure": "codemode"},
        )
        assert error is None, error
        manager.config = LoadedMcpConfig(servers=[parsed])
        manager._build_servers(manager.config)
        runtime.sync_mcp_tools(await manager.connect_all())
        assert "mcp__echo__echo" in {t.name for t in runtime.tools}

        # Same tool, newly hidden.

        runtime._mcp_tools = [replace_tool_hidden(t) for t in runtime._mcp_tools]
        runtime.sync_mcp_tools(runtime._mcp_tools)
        assert "mcp__echo__echo" not in {t.name for t in runtime.tools}
    finally:
        await runtime.close()


def replace_tool_hidden(tool):
    """The same tool object with a narrowed exposure, as a reload would build."""
    tool.exposure = "hidden"
    return tool

"""``ctx.sdk`` — the unified surface: one tool type, one hook path, one API.

These tests pin the contract that SDK, extension, and plugin-row code all speak
the same language: a ``BaseTool`` is a ``BaseTool`` no matter which door it
came through.
"""

from __future__ import annotations

import asyncio

import ctx.sdk as sdk
import pytest
from ctx.agent.tools.base import BaseTool
from ctx.plugins import boot


@pytest.fixture()
def composed(tmp_path, monkeypatch):
    monkeypatch.setattr("ctx.core.paths.get_config_dir", lambda: tmp_path)
    host = boot(auto_discover_extensions=False)
    yield host
    host.dispose()


def _greet(args, ctx):
    return f"hi {args['name']}"


_GREET_SCHEMA = {
    "type": "object",
    "properties": {"name": {"type": "string"}},
    "required": ["name"],
}


# ---- define_tool / SchemaTool -------------------------------------------------


def test_define_tool_produces_a_basetool_with_schema_params():
    t = sdk.define_tool("greet", "Greet", _GREET_SCHEMA, execute=_greet)
    assert isinstance(t, BaseTool)
    assert isinstance(t, sdk.SchemaTool)
    assert t.name == "greet"
    assert "name" in t.params.model_json_schema()["properties"]


def test_schema_tool_execute_normalizes_results():
    t = sdk.define_tool("greet", "Greet", _GREET_SCHEMA, execute=_greet)
    result = asyncio.run(t.execute(t.params(name="bob")))
    assert result.success
    assert result.result == "hi bob"


def test_schema_tool_execute_accepts_toolresult_and_none():
    async def main():
        tr = sdk.ToolResult(success=True, result="direct")
        t1 = sdk.define_tool("a", "", {"type": "object"}, execute=lambda a, c: tr)
        assert (await t1.execute(t1.params())).result == "direct"
        t2 = sdk.define_tool("b", "", {"type": "object"}, execute=lambda a, c: None)
        assert (await t2.execute(t2.params())).result == "(no output)"

    asyncio.run(main())


def test_agent_accepts_define_tool_and_tool_decorator_interchangeably():
    @sdk.tool
    def via_decorator(city: str) -> str:
        """Weather."""
        return city

    via_schema = sdk.define_tool("via_schema", "S", _GREET_SCHEMA, execute=_greet)
    agent = sdk.Agent(name="a", tools=[via_decorator, via_schema])
    names = {t.name for t in agent.compiled_tools()}
    assert names == {"via_decorator", "via_schema"}


# ---- the live composition ------------------------------------------------------


def test_context_and_api_require_a_running_composition(monkeypatch):
    monkeypatch.setattr("ctx.plugins.bootstrap._ACTIVE", None)
    with pytest.raises(RuntimeError, match="no ctx composition"):
        sdk.context()
    with pytest.raises(RuntimeError, match="no ctx composition"):
        sdk.api()


def test_context_returns_the_active_composition(composed):
    ctx = sdk.context()
    assert ctx is composed.base
    assert "tools" in ctx
    assert "llm" in ctx


def test_api_is_an_extension_api_bound_to_the_composition(composed):
    api = sdk.api()
    assert isinstance(api, sdk.ExtensionAPI)
    assert api._ctx is composed.base


def test_register_tool_accepts_every_spelling(composed):
    sdk.register_tool(sdk.define_tool("t_schema", "S", _GREET_SCHEMA, execute=_greet))
    sdk.register_tool(name="t_named", description="N", parameters=_GREET_SCHEMA, execute=_greet)

    @sdk.tool
    def via_decorator(name: str) -> str:
        """D."""
        return name

    sdk.register_tool(via_decorator)

    names = set(composed.base.tools._owned)
    assert {"t_schema", "t_named", "via_decorator"} <= names


def test_extension_api_register_tool_accepts_basetool(composed):
    api = sdk.api()
    api.register_tool(sdk.define_tool("ext_tool", "S", _GREET_SCHEMA, execute=_greet))
    assert "ext_tool" in composed.base.tools._owned


def test_register_command_and_dispatch(composed):
    sdk.register_command("sdk-hello", "Say hi", lambda args: f"hi {args}")
    commands = composed.base.get("commands")
    assert "sdk-hello" in commands.names()
    outcome = commands.dispatch("sdk-hello", "there")
    assert outcome.output == "hi there"


def test_on_subscribes_with_the_extension_event_contract(composed):
    seen = []

    @sdk.on(sdk.events.SESSION_START)
    def handler(event, payload):
        seen.append((event, payload))

    asyncio.run(sdk.emit(sdk.events.SESSION_START, {"cwd": "/tmp"}))
    assert seen and seen[0][0] == sdk.events.SESSION_START


def test_on_supports_the_three_arg_handler_form(composed):
    seen = []

    def handler(event, payload, ctx):
        seen.append(ctx)

    sdk.on(sdk.events.TURN_START, handler)
    asyncio.run(sdk.emit(sdk.events.TURN_START, {}))
    assert seen and isinstance(seen[0], sdk.ExtensionAPI)


def test_emit_nowait_schedules_dispatch(composed):
    async def main():
        seen = []
        sdk.on(sdk.events.AGENT_END, lambda event, payload: seen.append(payload))
        task = sdk.emit_nowait(sdk.events.AGENT_END, {"ok": True})
        assert task is not None
        await task
        assert seen == [{"ok": True}]

    asyncio.run(main())


def test_register_provider_routes_through_the_api(composed):
    # The TUI binds no pending_providers action, so this is a safe no-op here;
    # the contract being tested is that the call reaches api().register_provider.
    sdk.register_provider("my-provider", {"base_url": "http://x"})
    sdk.unregister_provider("my-provider")

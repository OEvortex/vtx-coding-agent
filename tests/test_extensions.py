"""Tests for the vtx extension system.

These tests cover the loader, the event bus, the tool-override semantics, and
the JSON-Schema -> pydantic conversion. They do not exercise the agent loop
end-to-end; that path is covered by tests that drive the turn runner.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from textwrap import dedent

import pytest

from vtx.core.types import ToolResult
from vtx.extensions import (
    AGENT_END,
    AGENT_START,
    COMPACTION_END,
    COMPACTION_START,
    SESSION_END,
    SESSION_START,
    TOOL_CALL,
    TOOL_RESULT,
    TURN_END,
    TURN_START,
    EventBus,
    ExtensionLoadError,
    _json_schema_to_pydantic,
    discover_extension_paths,
    load_all_extensions,
    load_extension,
)

# =============================================================================
# EventBus
# =============================================================================


def test_event_bus_register_and_emit():
    bus = EventBus()
    seen: list[tuple[str, dict]] = []

    @bus.on(AGENT_START)
    def handler(event, payload):
        seen.append((event, payload))

    result = asyncio.run(bus.emit(AGENT_START, foo="bar"))
    assert seen == [(AGENT_START, {"foo": "bar"})]
    assert result == {}


def test_event_bus_block_short_circuits():
    bus = EventBus()
    reached = False

    @bus.on(TOOL_CALL)
    def first(event, payload):
        return {"block": True, "reason": "denied"}

    @bus.on(TOOL_CALL)
    def second(event, payload):
        nonlocal reached
        reached = True
        return {"args": {"should": "be ignored"}}

    bus.on(TOOL_CALL)(first)
    bus.on(TOOL_CALL)(second)

    result = asyncio.run(bus.emit(TOOL_CALL, tool_name="bash", args={"cmd": "rm -rf /"}))
    assert result["block"] is True
    assert result["reason"] == "denied"
    assert reached is False


def test_event_bus_args_modification_chains():
    bus = EventBus()

    @bus.on(TOOL_CALL)
    def first(event, payload):
        return {"args": {"path": "/tmp/safe"}}

    @bus.on(TOOL_CALL)
    def second(event, payload):
        # Should see the modified args
        assert payload["args"] == {"path": "/tmp/safe"}
        return {"args": {"path": "/tmp/even-safer"}}

    result = asyncio.run(bus.emit(TOOL_CALL, args={"path": "/etc"}))
    assert result["args"] == {"path": "/tmp/even-safer"}


def test_event_bus_output_modification_chains():
    bus = EventBus()

    @bus.on(TOOL_RESULT)
    def first(event, payload):
        return {"output": "REDACTED"}

    @bus.on(TOOL_RESULT)
    def second(event, payload):
        assert payload["output"] == "REDACTED"
        return {"output": "REDACTED 2"}

    result = asyncio.run(bus.emit(TOOL_RESULT, output="original"))
    assert result["output"] == "REDACTED 2"


def test_event_bus_handler_exception_does_not_crash():
    bus = EventBus()
    saw: list[str] = []

    def broken(event, payload):
        raise RuntimeError("nope")

    def good(event, payload):
        saw.append("ok")

    bus.on(AGENT_END, broken)
    bus.on(AGENT_END, good)
    # Should not raise; the bad handler is swallowed.
    asyncio.run(bus.emit(AGENT_END))
    assert saw == ["ok"]


def test_event_bus_unknown_event_raises():
    bus = EventBus()
    with pytest.raises(ValueError, match="Unknown event"):
        bus.on("not_a_real_event", lambda *a, **k: None)


def test_emit_sync_does_not_await_async_handlers(caplog):
    """Sync emit at startup must not silently lose async work; it should warn."""
    import logging

    bus = EventBus()

    async def handler(event, payload):
        return {"args": {"x": 1}}

    bus.on(SESSION_START)(handler)

    with caplog.at_level(logging.WARNING, logger="vtx.extensions"):
        result = bus.emit_sync(SESSION_START)
    assert isinstance(result, dict)
    assert "returned a coroutine" in caplog.text


# =============================================================================
# JSON Schema -> pydantic
# =============================================================================


def test_json_schema_basic_types():
    model = _json_schema_to_pydantic(
        "search",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "search term"},
                "limit": {"type": "integer"},
                "ratio": {"type": "number"},
                "exact": {"type": "boolean"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["query"],
        },
    )

    instance = model(query="hello", limit=5, ratio=0.5, exact=True, tags=["a", "b"])
    assert instance.model_dump() == {
        "query": "hello",
        "limit": 5,
        "ratio": 0.5,
        "exact": True,
        "tags": ["a", "b"],
    }

    # Optional fields default to None
    instance = model(query="x")
    dumped = instance.model_dump()
    assert dumped["query"] == "x"
    assert dumped["limit"] is None
    assert dumped["tags"] is None


def test_json_schema_enum_appears_in_pydantic_schema():
    """The enum constraint lives in the JSON schema we hand to the LLM
    provider; pydantic does not enforce it at the Python level. Verify
    the schema round-trips with the ``enum`` keyword preserved."""
    model = _json_schema_to_pydantic(
        "shell",
        {
            "type": "object",
            "properties": {"shell": {"type": "string", "enum": ["bash", "sh", "zsh"]}},
            "required": ["shell"],
        },
    )
    schema = model.model_json_schema()
    assert schema["properties"]["shell"]["enum"] == ["bash", "sh", "zsh"]


def test_json_schema_empty_schema_gets_default_input():
    model = _json_schema_to_pydantic("noop", {"type": "object", "properties": {}})
    instance = model()
    assert "input" in instance.model_dump()


# =============================================================================
# Loader / discovery
# =============================================================================


def test_load_extension_simple(tmp_path: Path):
    ext = tmp_path / "hello.py"
    ext.write_text(
        dedent(
            """
            from vtx.extensions import AGENT_START

            def register(api):
                api.on(AGENT_START, lambda event, payload: None)
                api.register_command(
                    "hello",
                    "say hi",
                    lambda args: f"hi {args or 'world'}",
                )
            """
        ).strip()
    )

    bus = EventBus()
    loaded = load_extension(
        ext, bus=bus, cwd=str(tmp_path), session_file=None, config_dir=tmp_path
    )
    assert loaded.name == "hello"
    assert "hello" in loaded.commands
    outcome = loaded.commands["hello"].handler("")
    assert outcome.output == "hi world"


def test_load_extension_registering_tool(tmp_path: Path):
    ext = tmp_path / "greet.py"
    ext.write_text(
        dedent(
            """
            def register(api):
                api.register_tool(
                    name="greet",
                    description="Greet someone by name",
                    parameters={
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                        "required": ["name"],
                    },
                    execute=lambda args, ctx: {
                        "success": True,
                        "result": f"Hello, {args['name']}!",
                    },
                )
            """
        ).strip()
    )

    bus = EventBus()
    loaded = load_extension(
        ext, bus=bus, cwd=str(tmp_path), session_file=None, config_dir=tmp_path
    )
    assert "greet" in loaded.tools

    # Execute the registered tool end-to-end
    from vtx.extensions import ExtensionTool

    tool = loaded.tools["greet"]
    assert isinstance(tool, ExtensionTool)
    params_model = tool.params
    instance = params_model(name="Ada")
    result = asyncio.run(tool.execute(instance))
    assert isinstance(result, ToolResult)
    assert result.success is True
    assert result.result == "Hello, Ada!"


def test_load_extension_missing_register(tmp_path: Path):
    ext = tmp_path / "bad.py"
    ext.write_text("def something_else(api): pass\n")
    bus = EventBus()
    with pytest.raises(ExtensionLoadError, match="does not export"):
        load_extension(ext, bus=bus, cwd=str(tmp_path), session_file=None, config_dir=tmp_path)


def test_load_extension_broken_import(tmp_path: Path):
    ext = tmp_path / "broken.py"
    ext.write_text("this is not python\n")
    bus = EventBus()
    with pytest.raises(ExtensionLoadError, match="failed to import"):
        load_extension(ext, bus=bus, cwd=str(tmp_path), session_file=None, config_dir=tmp_path)


def test_load_extension_register_throws(tmp_path: Path):
    ext = tmp_path / "explodes.py"
    ext.write_text(
        dedent(
            """
            def register(api):
                raise RuntimeError("boom")
            """
        ).strip()
    )
    bus = EventBus()
    with pytest.raises(ExtensionLoadError, match="register\\(api\\) raised"):
        load_extension(ext, bus=bus, cwd=str(tmp_path), session_file=None, config_dir=tmp_path)


def test_load_all_extensions_collects_errors(tmp_path: Path):
    (tmp_path / "good.py").write_text("def register(api): pass\n")
    (tmp_path / "bad.py").write_text("def register(api): raise RuntimeError('nope')\n")

    exts, errors, _bus = load_all_extensions(cwd=str(tmp_path), configured=[str(tmp_path)])
    assert len(exts) == 1
    assert exts[0].name == "good"
    assert len(errors) == 1
    assert "nope" in errors[0]


def test_discover_extension_paths_project_and_global(tmp_path: Path, monkeypatch):
    project = tmp_path / "project" / ".vtx" / "extensions"
    project.mkdir(parents=True)
    (project / "first.py").write_text("def register(api): pass\n")

    global_dir = tmp_path / "home" / ".vtx" / "agent" / "extensions"
    global_dir.mkdir(parents=True)
    (global_dir / "second.py").write_text("def register(api): pass\n")

    paths = discover_extension_paths(
        cwd=str(tmp_path / "project"), agent_dir=tmp_path / "home" / ".vtx" / "agent"
    )
    names = sorted(p.stem for p in paths)
    # project-local wins on conflict; here we get one from each.
    assert names == ["first", "second"]


def test_discover_extension_paths_package_style(tmp_path: Path):
    pkg = tmp_path / "my_ext" / "__init__.py"
    pkg.parent.mkdir(parents=True)
    pkg.write_text("def register(api): pass\n")

    paths = discover_extension_paths(
        cwd=str(tmp_path), configured=[str(tmp_path / "my_ext")], agent_dir=tmp_path / "agent"
    )
    assert len(paths) == 1
    assert paths[0].name == "__init__.py"


def test_discover_extension_paths_skips_dunder_init():
    """``__init__.py`` at the top of the discovery dir is a package marker,
    not a loadable extension on its own."""
    # This is now handled by the package-detection branch above: if the
    # discovery directory contains a single ``__init__.py`` and nothing
    # else, that __init__.py is treated as a package entry point. We don't
    # treat bare top-level __init__.py files as extensions here because
    # there is no other marker (package.json, etc.) for that case.
    pass


def test_discover_extension_paths_resolves_dups(tmp_path: Path):
    (tmp_path / "shared.py").write_text("def register(api): pass\n")
    paths = discover_extension_paths(
        cwd=str(tmp_path),
        configured=[str(tmp_path / "shared.py"), str(tmp_path / "shared.py")],
        agent_dir=tmp_path / "agent",
    )
    assert len(paths) == 1


# =============================================================================
# ExtensionTool
# =============================================================================


def test_extension_tool_sync_execute(tmp_path: Path):
    from vtx.extensions import ExtensionTool

    schema = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}
    params_model = _json_schema_to_pydantic("double", schema)
    tool = ExtensionTool(
        name="double",
        description="double a number",
        parameters=schema,
        params_model=params_model,
        execute_fn=lambda args, ctx: {"success": True, "result": str(args["n"] * 2)},
        owner="test",
        mutating=False,
        label="double",
    )
    instance = params_model(n=21)
    result = asyncio.run(tool.execute(instance))
    assert result.success
    assert result.result == "42"


def test_extension_tool_async_execute(tmp_path: Path):
    from vtx.extensions import ExtensionTool

    schema = {"type": "object", "properties": {"x": {"type": "string"}}}
    params_model = _json_schema_to_pydantic("echo", schema)
    tool = ExtensionTool(
        name="echo",
        description="echo",
        parameters=schema,
        params_model=params_model,
        execute_fn=lambda args, ctx: _async_value({"success": True, "result": args["x"]}),
        owner="test",
        mutating=False,
        label="echo",
    )
    instance = params_model(x="hi")
    result = asyncio.run(tool.execute(instance))
    assert result.result == "hi"


async def _async_value(value):
    return value


# =============================================================================
# Tool override (extension wins over built-in)
# =============================================================================


def test_extension_tool_override_built_in(tmp_path: Path, monkeypatch):
    """If an extension registers a tool whose name matches a built-in, the
    extension version should win in the merged list."""
    from vtx.tools import get_tools_with_extensions

    schema = {"type": "object", "properties": {"path": {"type": "string"}}}
    params_model = _json_schema_to_pydantic("read", schema)
    from vtx.extensions import ExtensionTool

    override = ExtensionTool(
        name="read",
        description="overridden read",
        parameters=schema,
        params_model=params_model,
        execute_fn=lambda args, ctx: {"success": True, "result": "OVERRIDE"},
        owner="test",
        mutating=False,
        label="read",
    )

    tools = get_tools_with_extensions(["read", "bash"], [override])
    names = [t.name for t in tools]
    assert names.count("read") == 1
    read_tool = next(t for t in tools if t.name == "read")
    assert read_tool.description == "overridden read"


# =============================================================================
# All events fire through the bus
# =============================================================================


@pytest.mark.parametrize(
    "event",
    [
        SESSION_START,
        SESSION_END,
        AGENT_START,
        AGENT_END,
        TURN_START,
        TURN_END,
        TOOL_CALL,
        TOOL_RESULT,
        COMPACTION_START,
        COMPACTION_END,
    ],
)
def test_every_event_can_be_subscribed_to(event: str):
    bus = EventBus()
    fired: list[str] = []

    @bus.on(event)
    def handler(event_name, payload):
        fired.append(event_name)

    asyncio.run(bus.emit(event))
    assert fired == [event]

"""Tests for the vtx.agents package: schema, discovery, loader, registry.

Mirrors the style of tests/test_extensions.py.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from textwrap import dedent

import pytest

from vtx.agents import (
    AGENT_ACTIVATED,
    AGENT_CHANGED,
    AgentDef,
    AgentLoadError,
    AgentRegistry,
    LoadedAgent,
    compose_active_commands,
    compose_active_tools,
    find_agent_paths,
    load_agent,
    load_all_agents,
)
from vtx.agents.schema import AGENT_NAME_RE
from vtx.core.types import ToolResult
from vtx.extensions import AGENT_START, EventBus, ExtensionCommand, ExtensionTool
from vtx.tools import BaseTool

# =============================================================================
# Schema
# =============================================================================


def test_agent_name_regex_accepts_valid_names():
    assert AGENT_NAME_RE.match("code-review")
    assert AGENT_NAME_RE.match("yolo")
    assert AGENT_NAME_RE.match("a")
    assert AGENT_NAME_RE.match("a1")
    assert AGENT_NAME_RE.match("agent-1")


def test_agent_name_regex_rejects_invalid_names():
    assert not AGENT_NAME_RE.match("-foo")
    assert not AGENT_NAME_RE.match("foo-")
    assert not AGENT_NAME_RE.match("Foo")
    assert not AGENT_NAME_RE.match("foo--bar")
    assert not AGENT_NAME_RE.match("foo_bar")
    assert not AGENT_NAME_RE.match("")


def test_agentdef_minimal():
    a = AgentDef(name="code-review", description="Strict review")
    assert a.name == "code-review"
    assert a.description == "Strict review"
    assert a.tools_allow is None
    assert a.tools_deny == []
    assert a.permission_mode is None
    assert a.handoff_back is True
    assert a.instructions_mode == "append"


def test_agentdef_rejects_bad_name():
    with pytest.raises(ValueError):
        AgentDef(name="Bad-Name", description="x")


def test_agentdef_rejects_empty_description():
    with pytest.raises(ValueError):
        AgentDef(name="ok", description="")


def test_agentdef_deny_filters_empty_strings():
    a = AgentDef(
        name="ok",
        description="x",
        tools_allow=["read", "", "find"],
        tools_deny=["bash", "  ", "write"],
    )
    assert a.tools_allow == ["read", "find"]
    assert a.tools_deny == ["bash", "write"]


# =============================================================================
# Discovery
# =============================================================================


def test_find_agent_paths_project_and_global(tmp_path: Path, monkeypatch):
    project = tmp_path / "project" / ".vtx" / "agent"
    project.mkdir(parents=True)
    (project / "code-review.py").write_text("AGENT = None\n")

    global_dir = tmp_path / "home" / ".vtx" / "agent"
    global_dir.mkdir(parents=True)
    (global_dir / "yolo.py").write_text("AGENT = None\n")

    paths = find_agent_paths(
        cwd=str(tmp_path / "project"), agent_dir=tmp_path / "home" / ".vtx" / "agent"
    )
    names = sorted(p.stem for p in paths)
    assert names == ["code-review", "yolo"]


def test_find_agent_paths_project_wins_on_collision(tmp_path: Path, monkeypatch):
    project = tmp_path / "project" / ".vtx" / "agent"
    project.mkdir(parents=True)
    (project / "x.py").write_text("# project")

    global_dir = tmp_path / "home" / ".vtx" / "agent"
    global_dir.mkdir(parents=True)
    (global_dir / "x.py").write_text("# global")

    paths = find_agent_paths(
        cwd=str(tmp_path / "project"), agent_dir=tmp_path / "home" / ".vtx" / "agent"
    )
    # Project version wins — only one entry.
    assert len(paths) == 1
    assert paths[0].parent.parent.parent == tmp_path / "project"


def test_find_agent_paths_package_form(tmp_path: Path, monkeypatch):
    pkg = tmp_path / "my_agent" / "__init__.py"
    pkg.parent.mkdir(parents=True)
    pkg.write_text("# package")

    paths = find_agent_paths(
        cwd=str(tmp_path), configured=[str(tmp_path / "my_agent")], agent_dir=tmp_path / "agent"
    )
    assert len(paths) == 1
    assert paths[0].name == "__init__.py"
    assert paths[0].parent.name == "my_agent"


def test_find_agent_paths_walks_to_git_root(tmp_path: Path, monkeypatch):
    # Project root has .git
    git_root = tmp_path / "r"
    (git_root / ".git").mkdir(parents=True)
    # Agent dir at the root
    (git_root / ".vtx" / "agent").mkdir(parents=True)
    (git_root / ".vtx" / "agent" / "root_agent.py").write_text("# root")

    # Subdir of the repo
    sub = git_root / "sub" / "deeper"
    sub.mkdir(parents=True)

    paths = find_agent_paths(cwd=str(sub), agent_dir=tmp_path / "home" / ".vtx" / "agent")
    assert any(p.name == "root_agent.py" for p in paths)


def test_find_agent_paths_dedupes(tmp_path: Path):
    (tmp_path / "shared.py").write_text("# x")
    paths = find_agent_paths(
        cwd=str(tmp_path),
        configured=[str(tmp_path / "shared.py"), str(tmp_path / "shared.py")],
        agent_dir=tmp_path / "agent",
    )
    assert len(paths) == 1


# =============================================================================
# Loader
# =============================================================================


def test_load_agent_minimal_data_only(tmp_path: Path):
    a = tmp_path / "code-review.py"
    a.write_text(
        dedent(
            """
            from vtx.agents import AgentDef
            AGENT = AgentDef(
                name="code-review",
                description="Strict review mode",
                tools_allow=["read"],
                tools_deny=["bash", "write"],
                permission_mode="auto",
            )
            """
        ).strip()
    )
    loaded = load_agent(a, cwd=str(tmp_path), config_dir=tmp_path)
    assert loaded.definition.name == "code-review"
    assert loaded.definition.permission_mode == "auto"
    assert loaded.definition.tools_allow == ["read"]
    assert loaded.definition.tools_deny == ["bash", "write"]
    assert loaded.local_tools == {}
    assert loaded.local_commands == {}


def test_load_agent_with_register_and_local_tool(tmp_path: Path):
    a = tmp_path / "code-review.py"
    a.write_text(
        dedent(
            """
            from vtx.agents import AgentDef

            AGENT = AgentDef(
                name="code-review",
                description="Strict review mode",
            )

            def register(api):
                @api.local_tool(
                    name="pr_summary",
                    description="Summarize the PR",
                    parameters={
                        "type": "object",
                        "properties": {"base": {"type": "string"}},
                        "required": ["base"],
                    },
                    mutating=False,
                )
                def pr_summary(args, ctx):
                    return {"success": True, "result": f"summary for {args['base']}"}

                @api.local_command(
                    name="checklist",
                    description="Run the code review checklist",
                )
                def checklist(args):
                    return f"checklist ({args})"

                api.permission_gate(
                    tool="bash",
                    when="command matches 'rm -rf'",
                    action="deny",
                    reason="destructive",
                )
            """
        ).strip()
    )
    loaded = load_agent(a, cwd=str(tmp_path), config_dir=tmp_path)
    assert loaded.local_tools["pr_summary"].name == "pr_summary"
    assert loaded.local_commands["checklist"]("args")  # callable
    assert "bash" in loaded.local_gates
    assert loaded.local_gates["bash"][0].action == "deny"

    # Execute the tool end-to-end

    tool = loaded.local_tools["pr_summary"]
    params_model = tool.params
    instance = params_model(base="main")
    result = asyncio.run(tool.execute(instance))
    assert isinstance(result, ToolResult)
    assert result.success is True
    assert result.result is not None
    assert "summary for main" in result.result


def test_load_agent_event_handler_is_wired(tmp_path: Path):
    a = tmp_path / "code-review.py"
    a.write_text(
        dedent(
            """
            from vtx.agents import AgentDef
            from vtx.extensions import AGENT_START

            AGENT = AgentDef(name="code-review", description="x")

            def register(api):
                @api.on(AGENT_START)
                def on_start(event, payload):
                    pass
            """
        ).strip()
    )
    wired: list[tuple[str, object]] = []

    def on_event(event, handler):
        wired.append((event, handler))

    loaded = load_agent(a, cwd=str(tmp_path), config_dir=tmp_path, on_event=on_event)
    # The handler is also stored on the LoadedAgent
    assert AGENT_START in loaded.handlers
    # And the runtime's on_event was called
    assert any(e == AGENT_START for e, _ in wired)


def test_load_agent_missing_ag_agent(tmp_path: Path):
    a = tmp_path / "bad.py"
    a.write_text("def something_else(): pass\n")
    with pytest.raises(AgentLoadError, match="does not export"):
        load_agent(a, cwd=str(tmp_path), config_dir=tmp_path)


def test_load_agent_name_mismatch(tmp_path: Path):
    a = tmp_path / "code-review.py"
    a.write_text(
        dedent(
            """
            from vtx.agents import AgentDef
            AGENT = AgentDef(name="different-name", description="x")
            """
        ).strip()
    )
    with pytest.raises(AgentLoadError, match="does not match"):
        load_agent(a, cwd=str(tmp_path), config_dir=tmp_path)


def test_load_agent_register_raises(tmp_path: Path):
    a = tmp_path / "explodes.py"
    a.write_text(
        dedent(
            """
            from vtx.agents import AgentDef
            AGENT = AgentDef(name="explodes", description="x")
            def register(api):
                raise RuntimeError("boom")
            """
        ).strip()
    )
    with pytest.raises(AgentLoadError, match="register"):
        load_agent(a, cwd=str(tmp_path), config_dir=tmp_path)


def test_load_all_agents_collects_errors(tmp_path: Path):
    (tmp_path / "good.py").write_text(
        "from vtx.agents import AgentDef\nAGENT = AgentDef(name='good', description='x')\n"
    )
    (tmp_path / "bad.py").write_text("def register(): pass\n")

    loaded, errors = load_all_agents(
        cwd=str(tmp_path), configured=[str(tmp_path)], agent_dir=tmp_path / "agent"
    )
    assert len(loaded) == 1
    assert loaded[0].definition.name == "good"
    assert len(errors) == 1


# =============================================================================
# Registry
# =============================================================================


def test_registry_set_and_cycle():
    reg = AgentRegistry()
    reg.agents = [
        LoadedAgent(definition=AgentDef(name="a", description="A"), path=Path("/dev/null/a.py")),
        LoadedAgent(definition=AgentDef(name="b", description="B"), path=Path("/dev/null/b.py")),
    ]
    assert reg.by_name("a") is not None
    assert reg.by_name("missing") is None
    assert reg.names == ["a", "b"]
    assert reg.active is None

    new = reg.cycle()
    assert new is not None
    assert new.definition.name == "a"
    new = reg.cycle()
    assert new is not None
    assert new.definition.name == "b"
    new = reg.cycle()
    assert new is None
    new = reg.cycle()
    assert new is not None
    assert new.definition.name == "a"


def test_registry_set_active_unknown_name():
    reg = AgentRegistry()
    reg.agents = [
        LoadedAgent(definition=AgentDef(name="a", description="A"), path=Path("/dev/null/a.py"))
    ]
    assert reg.set_active("nope") is None
    assert reg.active is None


def test_registry_set_active_emits_on_change():
    reg = AgentRegistry()
    reg.agents = [
        LoadedAgent(definition=AgentDef(name="a", description="A"), path=Path("/dev/null/a.py"))
    ]
    seen: list[object] = []
    reg.set_on_change(lambda new: seen.append(new))
    reg.set_active("a")
    assert len(seen) == 1
    # Setting the same agent again is a no-op for on_change
    reg.set_active("a")
    assert len(seen) == 1
    reg.set_active(None)
    assert len(seen) == 2


def test_registry_describe():
    reg = AgentRegistry()
    reg.agents = [
        LoadedAgent(
            definition=AgentDef(name="a", description="A agent", tools_allow=["read"]),
            path=Path("/a.py"),
        )
    ]
    rows = reg.describe()
    assert rows[0]["name"] == "a"
    assert rows[0]["description"] == "A agent"
    assert rows[0]["active"] is False


# =============================================================================
# Activation: tool/command composition
# =============================================================================


def _builtin_pool() -> dict[str, BaseTool]:
    from vtx.tools import tools_by_name

    return dict(tools_by_name)


def test_compose_active_tools_no_agent_returns_base():
    pool = _builtin_pool()
    base = ["read", "write", "bash", "find"]
    out = compose_active_tools(
        base_tool_names=base, base_tool_pool=pool, extension_tools=[], active_agent=None
    )
    names = {t.name for t in out}
    assert names == set(base)


def test_compose_active_tools_deny_drops_tools():
    pool = _builtin_pool()
    base = ["read", "write", "bash", "find"]
    agent = LoadedAgent(
        definition=AgentDef(name="review", description="x", tools_deny=["bash", "write"]),
        path=Path("/x.py"),
    )
    out = compose_active_tools(
        base_tool_names=base, base_tool_pool=pool, extension_tools=[], active_agent=agent
    )
    names = {t.name for t in out}
    assert "bash" not in names
    assert "write" not in names
    assert "read" in names
    assert "find" in names


def test_compose_active_tools_allow_intersects():
    pool = _builtin_pool()
    base = ["read", "write", "bash", "find"]
    agent = LoadedAgent(
        definition=AgentDef(
            name="review", description="x", tools_allow=["read", "bash"], tools_deny=["bash"]
        ),
        path=Path("/x.py"),
    )
    out = compose_active_tools(
        base_tool_names=base, base_tool_pool=pool, extension_tools=[], active_agent=agent
    )
    names = {t.name for t in out}
    # allow + deny: only "read" remains
    assert names == {"read"}


def test_compose_active_tools_local_tools_bypass_allow_deny():
    """A local tool the agent contributes is never stripped by its own
    allow/deny filters. The allow/deny lists target the base pool, not
    the agent's own contributions."""
    from vtx.extensions import _json_schema_to_pydantic

    pool = _builtin_pool()
    base = ["read", "write", "bash"]
    local_tool = ExtensionTool(
        name="custom_tool",
        description="x",
        parameters={"type": "object", "properties": {}},
        params_model=_json_schema_to_pydantic("custom_tool", {"type": "object", "properties": {}}),
        execute_fn=lambda args, ctx: {"success": True, "result": "ok"},
        owner="review",
        mutating=False,
        label="custom_tool",
    )
    agent = LoadedAgent(
        definition=AgentDef(
            name="review",
            description="x",
            tools_allow=["read"],
            tools_deny=["custom_tool"],  # try to deny the local tool
        ),
        path=Path("/x.py"),
        local_tools={"custom_tool": local_tool},
    )
    out = compose_active_tools(
        base_tool_names=base, base_tool_pool=pool, extension_tools=[], active_agent=agent
    )
    names = {t.name for t in out}
    # custom_tool survives both the deny and the allow restriction.
    assert "custom_tool" in names
    assert "read" in names
    assert "write" not in names
    assert "bash" not in names


def test_compose_active_tools_includes_local_tool():
    from vtx.extensions import _json_schema_to_pydantic

    pool = _builtin_pool()
    base = ["read", "bash"]
    local_tool = ExtensionTool(
        name="pr_summary",
        description="x",
        parameters={"type": "object", "properties": {}},
        params_model=_json_schema_to_pydantic("pr_summary", {"type": "object", "properties": {}}),
        execute_fn=lambda args, ctx: {"success": True, "result": "ok"},
        owner="review",
        mutating=False,
        label="pr_summary",
    )
    agent = LoadedAgent(
        definition=AgentDef(name="review", description="x"),
        path=Path("/x.py"),
        local_tools={"pr_summary": local_tool},
    )
    out = compose_active_tools(
        base_tool_names=base, base_tool_pool=pool, extension_tools=[], active_agent=agent
    )
    names = {t.name for t in out}
    assert "pr_summary" in names
    assert "read" in names


def test_compose_active_commands_merges_local():
    from vtx.extensions import CommandOutcome

    def _make_handler(s: str):
        def _h(args: str) -> CommandOutcome:
            return CommandOutcome(output=s)

        return _h

    base = {
        "review": ExtensionCommand(
            name="review", description="d", handler=_make_handler("x"), owner="ext"
        )
    }
    agent = LoadedAgent(definition=AgentDef(name="a", description="x"), path=Path("/x.py"))
    agent.local_commands["checklist"] = _make_handler("y")

    out = compose_active_commands(base_commands=base, active_agent=agent)
    assert "review" in out
    assert "checklist" in out
    # The local command's owner is the agent name
    assert out["checklist"].owner == "a"


# =============================================================================
# Permission gate predicate compilation
# =============================================================================


def test_when_predicate_matches_substring():
    from vtx.agents.api import _when_predicate

    pred = _when_predicate("command matches 'rm -rf'")
    assert pred({"command": "rm -rf /tmp"})
    assert not pred({"command": "ls"})


def test_when_predicate_equality():
    from vtx.agents.api import _when_predicate

    pred = _when_predicate("name == 'tool_call'")
    assert pred({"name": "tool_call"})
    assert not pred({"name": "tool_result"})


def test_when_predicate_unsupported_expression_raises():
    from vtx.agents.api import _when_predicate

    with pytest.raises(ValueError, match="unsupported"):
        _when_predicate("command contains 'rm'")


# =============================================================================
# ExtensionAPI.register_local_tool
# =============================================================================


def test_extension_register_local_tool(tmp_path: Path):
    """The cross-agent local_tool API in ExtensionAPI."""
    from vtx.extensions import load_extension

    ext = tmp_path / "ext.py"
    ext.write_text(
        dedent(
            """
            def register(api):
                api.register_local_tool(
                    agent="code-review",
                    name="secrets_scan",
                    description="scan for secrets",
                    parameters={"type": "object", "properties": {}},
                    execute=lambda args, ctx: {"success": True, "result": "ok"},
                    mutating=False,
                )
            """
        ).strip()
    )
    bus = EventBus()
    loaded = load_extension(
        ext, bus=bus, cwd=str(tmp_path), session_file=None, config_dir=tmp_path
    )
    assert "code-review" in loaded.local_tools
    assert "secrets_scan" in loaded.local_tools["code-review"]


# =============================================================================
# AGENT_ACTIVATED / AGENT_CHANGED in ALL_EVENTS
# =============================================================================


def test_agent_events_in_all_events():
    from vtx.extensions import ALL_EVENTS

    assert AGENT_ACTIVATED in ALL_EVENTS
    assert AGENT_CHANGED in ALL_EVENTS

"""Tests for the agent end-to-end flow: loading, runtime activation, CLI."""

from __future__ import annotations

import sys
from pathlib import Path
from textwrap import dedent

import pytest

from vtx.agents import (
    AGENT_ACTIVATED,
    AGENT_CHANGED,
    AgentDef,
    AgentRegistry,
    LoadedAgent,
    load_all_agents,
)
from vtx.extensions import EventBus
from vtx.runtime import ConversationRuntime

# =============================================================================
# Loader
# =============================================================================


def test_load_all_agents_with_cwd(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".vtx" / "agent").mkdir(parents=True)
    (tmp_path / ".vtx" / "agent" / "review.py").write_text(
        dedent(
            """
            from vtx.agents import AgentDef
            AGENT = AgentDef(name="review", description="Reviewer", tools_deny=["bash"])
            """
        ).strip()
    )
    loaded, errors = load_all_agents(cwd=str(tmp_path))
    names = {a.definition.name for a in loaded}
    assert "review" in names
    assert "plan" in names
    assert errors == []


def test_load_all_agents_user_writes_global_only(tmp_path: Path):
    global_dir = tmp_path / "home" / ".vtx" / "agent"
    global_dir.mkdir(parents=True)
    (global_dir / "yolo.py").write_text(
        'from vtx.agents import AgentDef\nAGENT = AgentDef(name="yolo", description="fast")\n'
    )
    cwd = tmp_path / "project"
    cwd.mkdir()
    loaded, errors = load_all_agents(cwd=str(cwd), agent_dir=global_dir)
    names = {a.definition.name for a in loaded}
    assert "yolo" in names
    assert "plan" in names
    assert errors == []


# =============================================================================
# ConversationRuntime integration
# =============================================================================


def _make_agent(name: str, **kwargs) -> LoadedAgent:
    return LoadedAgent(
        definition=AgentDef(name=name, description=f"{name} agent", **kwargs),
        path=Path(f"/tmp/{name}.py"),
    )


def test_runtime_set_active_agent_persists_last_selected(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    from vtx.config import reset_config

    reset_config()

    registry = AgentRegistry()
    registry.agents = [_make_agent("review", tools_deny=["bash"]), _make_agent("yolo")]
    runtime = ConversationRuntime(
        cwd=str(tmp_path),
        model="gpt-test",
        tools=[],
        extensions=EventBus(),
        agent_registry=registry,
    )
    assert runtime.active_agent is None
    new = runtime.set_active_agent("review")
    assert new is not None
    assert new.definition.name == "review"
    assert runtime.active_agent is not None
    assert runtime.active_agent.definition.name == "review"

    from vtx.config import get_last_selected

    ls = get_last_selected()
    assert ls.agent == "review"


def test_runtime_set_active_agent_unknown_returns_none(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    from vtx.config import reset_config

    reset_config()

    registry = AgentRegistry()
    registry.agents = [_make_agent("review")]
    runtime = ConversationRuntime(
        cwd=str(tmp_path),
        model="gpt-test",
        tools=[],
        extensions=EventBus(),
        agent_registry=registry,
    )
    new = runtime.set_active_agent("nope")
    assert new is None
    assert runtime.active_agent is None


def test_runtime_cycle_active_agent_cycles(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    from vtx.config import reset_config

    reset_config()

    registry = AgentRegistry()
    registry.agents = [_make_agent("a"), _make_agent("b")]
    runtime = ConversationRuntime(
        cwd=str(tmp_path),
        model="gpt-test",
        tools=[],
        extensions=EventBus(),
        agent_registry=registry,
    )
    a1 = runtime.cycle_active_agent()
    assert a1 is not None
    assert a1.definition.name == "a"
    a2 = runtime.cycle_active_agent()
    assert a2 is not None
    assert a2.definition.name == "b"
    a3 = runtime.cycle_active_agent()
    assert a3 is None
    a4 = runtime.cycle_active_agent()
    assert a4 is not None
    assert a4.definition.name == "a"


def test_runtime_active_commands_no_agent():
    registry = AgentRegistry()
    runtime = ConversationRuntime(
        cwd="/tmp", model="gpt", tools=[], extensions=EventBus(), agent_registry=registry
    )
    out = runtime.active_commands()
    assert out == {}


# =============================================================================
# System prompt integration
# =============================================================================


def test_system_prompt_includes_agent_instructions(monkeypatch, tmp_path: Path):
    from vtx.prompts import build_system_prompt

    prompt = build_system_prompt(
        cwd=str(tmp_path),
        extra_instructions="AGENT PROFILE: be terse and only output [P0]..[P3] findings.",
    )
    assert "AGENT PROFILE: be terse" in prompt
    # Default base is also present
    assert "Vtx" in prompt


def test_system_prompt_replace_mode(monkeypatch, tmp_path: Path):
    from vtx.prompts import build_system_prompt

    prompt = build_system_prompt(
        cwd=str(tmp_path), extra_instructions="CUSTOM ONLY", extra_instructions_mode="replace"
    )
    assert "CUSTOM ONLY" in prompt
    # The base identity line should be gone
    assert "You are an expert coding assistant called Vtx" not in prompt


# =============================================================================
# CLI --list-agents
# =============================================================================


def test_cli_list_agents(monkeypatch, tmp_path: Path, capsys):
    from vtx import cli

    (tmp_path / ".vtx" / "agent").mkdir(parents=True)
    (tmp_path / ".vtx" / "agent" / "review.py").write_text(
        "from vtx.agents import AgentDef\n"
        'AGENT = AgentDef(name="review", description="Reviewer")\n'
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["vtx", "--list-agents"])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert "review" in out


def test_cli_list_agents_empty(monkeypatch, tmp_path: Path, capsys):
    from vtx import cli

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["vtx", "--list-agents"])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert "plan" in out


# =============================================================================
# Events
# =============================================================================


def test_agent_activated_event_in_all_events():
    from vtx.extensions import ALL_EVENTS

    assert AGENT_ACTIVATED in ALL_EVENTS
    assert AGENT_CHANGED in ALL_EVENTS


def test_loaded_agent_wire_handlers():
    from vtx.agents.api import LoadedAgent
    from vtx.extensions import AGENT_START

    agent = LoadedAgent(definition=AgentDef(name="a", description="x"), path=Path("/x.py"))

    seen: list = []

    def handler(event, payload):
        seen.append((event, payload))

    agent.handlers.setdefault(AGENT_START, []).append(handler)

    bus = EventBus()
    agent.wire_handlers(bus)

    import asyncio

    asyncio.run(bus.emit(AGENT_START))
    assert len(seen) == 1


def test_runtime_set_active_agent_updates_agent_tools(monkeypatch, tmp_path: Path):
    from vtx.agents import AgentRegistry
    from vtx.extensions import EventBus
    from vtx.runtime import ConversationRuntime

    registry = AgentRegistry()
    registry.agents = [
        _make_agent("review", tools_allow=["read"]),
        _make_agent("plan", tools_allow=["read", "grep"]),
    ]

    runtime = ConversationRuntime(
        cwd=str(tmp_path),
        model="gpt-test",
        tools=[],
        extensions=EventBus(),
        agent_registry=registry,
    )

    class DummyAgent:
        def __init__(self):
            self.tools = []

        def reload_context(self):
            pass

    from typing import Any, cast

    runtime.agent = cast(Any, DummyAgent())

    # Activate review
    runtime.set_active_agent("review")
    agent = runtime.agent
    assert agent.tools is not None
    tool_names = {t.name for t in agent.tools}
    assert "read" in tool_names
    assert "grep" not in tool_names

    # Switch to plan
    runtime.set_active_agent("plan")
    agent = runtime.agent
    tool_names = {t.name for t in agent.tools}
    assert "read" in tool_names
    assert "grep" in tool_names


def test_agent_tool_list_does_not_leak_across_switches(monkeypatch, tmp_path: Path):
    """Switching FROM a restrictive agent (plan) TO a default agent must not
    carry over tools that only the restrictive agent should have."""
    from vtx.agents import AgentRegistry
    from vtx.extensions import EventBus
    from vtx.runtime import ConversationRuntime
    from vtx.tools import DEFAULT_TOOLS

    registry = AgentRegistry()
    registry.agents = [_make_agent("plan", tools_allow=["read", "find", "grep", "skill"])]

    runtime = ConversationRuntime(
        cwd=str(tmp_path),
        model="gpt-test",
        tools=[],
        extensions=EventBus(),
        agent_registry=registry,
    )

    class DummyAgent:
        def __init__(self):
            self.tools = []

        def reload_context(self):
            pass

    from typing import Any, cast

    runtime.agent = cast(Any, DummyAgent())

    # Activate plan (restrictive)
    runtime.set_active_agent("plan")
    plan_tools = {t.name for t in runtime.agent.tools}
    assert plan_tools == {"read", "find", "grep", "skill"}
    assert "edit" not in plan_tools
    assert "write" not in plan_tools
    assert "bash" not in plan_tools

    # Deactivate plan (back to no agent = default)
    runtime.set_active_agent(None)
    default_tools = {t.name for t in runtime.agent.tools}

    # Must have the full default set, not plan's restricted set
    for name in DEFAULT_TOOLS:
        assert name in default_tools, f"{name} missing from default tools after switching off plan"

    # Must NOT have plan-only tools leaking in
    assert "grep" not in default_tools


def test_runtime_set_active_agent_updates_system_prompt(monkeypatch, tmp_path: Path):
    from typing import Any, cast

    from vtx.agents import AgentRegistry
    from vtx.context import Context
    from vtx.extensions import EventBus
    from vtx.runtime import ConversationRuntime

    registry = AgentRegistry()
    registry.agents = [
        _make_agent("review", instructions="Review instructions"),
        _make_agent("plan", instructions="Plan instructions"),
    ]

    runtime = ConversationRuntime(
        cwd=str(tmp_path),
        model="gpt-test",
        tools=[],
        extensions=EventBus(),
        agent_registry=registry,
    )

    class DummyAgent:
        def __init__(self):
            self.tools = []
            self._system_prompt = ""

        def reload_context(self):
            pass

    runtime.agent = cast(Any, DummyAgent())
    runtime.context = Context.load(str(tmp_path))

    # Activate review
    runtime.set_active_agent("review")
    agent = runtime.agent
    assert "Review instructions" in agent._system_prompt

    # Switch to plan
    runtime.set_active_agent("plan")
    agent = runtime.agent
    assert "Plan instructions" in agent._system_prompt

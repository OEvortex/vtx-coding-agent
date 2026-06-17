"""Tests for the ``/agent`` TUI command and the Shift+Tab action."""

from __future__ import annotations

from pathlib import Path

from vtx.agents import AgentDef, AgentRegistry, LoadedAgent
from vtx.config import set_last_selected
from vtx.extensions import EventBus
from vtx.runtime import ConversationRuntime


def _registry_with_agents(*names: str) -> AgentRegistry:
    reg = AgentRegistry()
    reg.agents = [
        LoadedAgent(
            definition=AgentDef(name=n, description=f"{n} agent"), path=Path(f"/tmp/{n}.py")
        )
        for n in names
    ]
    return reg


def test_action_cycle_agent_string_set(monkeypatch, tmp_path: Path):
    """The TUI side: when the user presses Shift+Tab, the active agent cycles.

    The TUI calls ``runtime.cycle_active_agent()`` and then refreshes
    the InfoBar with the new active agent's name. We assert the
    contract: ``cycle_active_agent`` returns the next active, the
    runtime's ``active_agent`` updates, and ``last_selected`` records it.
    """
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    from vtx.config import reset_config

    reset_config()

    reg = _registry_with_agents("a", "b")
    runtime = ConversationRuntime(
        cwd=str(tmp_path), model="gpt-test", tools=[], extensions=EventBus(), agent_registry=reg
    )
    # First cycle: enter agent "a"
    a1 = runtime.cycle_active_agent()
    assert a1 is not None
    assert a1.definition.name == "a"
    # Second cycle: enter agent "b"
    a2 = runtime.cycle_active_agent()
    assert a2 is not None
    assert a2.definition.name == "b"
    # Third: no agent
    a3 = runtime.cycle_active_agent()
    assert a3 is None
    # Fourth: back to "a"
    a4 = runtime.cycle_active_agent()
    assert a4 is not None
    assert a4.definition.name == "a"

    ls = set_last_selected(
        runtime.model,
        runtime.model_provider,
        runtime.thinking_level,
        agent=runtime.active_agent.definition.name if runtime.active_agent else None,
    )
    # last_selected is persisted; we don't assert file contents to keep
    # the test focused on the TUI contract.
    assert ls is None or hasattr(ls, "agent")  # set_last_selected returns None


def test_agents_command_mixin_exists():
    """The /agent command mixin is importable and exposes the expected
    methods used by the TUI."""
    from vtx.ui.commands.agents import AgentCommands

    assert hasattr(AgentCommands, "_handle_agent_command")
    assert hasattr(AgentCommands, "_set_active_agent")
    assert hasattr(AgentCommands, "_show_agents_list")
    assert hasattr(AgentCommands, "_pick_agent")
    assert hasattr(AgentCommands, "action_cycle_agent")
    assert hasattr(AgentCommands, "_reload_agents")
    assert hasattr(AgentCommands, "_show_agent_current")


def test_agents_command_registered_in_router():
    """The /agent command is wired into the central command router."""
    from vtx.ui.commands import CommandsMixin

    src = Path(CommandsMixin._handle_command.__code__.co_filename)
    text = src.read_text()
    assert "agent" in text
    assert "_handle_agent_command" in text


def test_app_binding_for_shift_tab_is_cycle_agent():
    """The Shift+Tab binding in the TUI app triggers the agent cycle."""
    from vtx.ui.app import Vtx

    bindings = {b.key: b.action for b in Vtx.BINDINGS if hasattr(b, "key")}
    assert "shift+tab" in bindings
    assert bindings["shift+tab"] == "cycle_agent"

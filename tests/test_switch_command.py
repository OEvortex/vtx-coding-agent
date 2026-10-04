"""Tests for the /switch slash command."""

from __future__ import annotations

from vtx.tui.commands.switch import SwitchCommands


def test_switch_delegates_to_agent_handler(monkeypatch):
    """``/switch <name>`` delegates to ``_handle_agent_command``."""
    mixin = SwitchCommands()
    calls: list[str] = []
    monkeypatch.setattr(mixin, "_handle_agent_command", calls.append)

    mixin._handle_switch_command("crs")

    assert calls == ["crs"]


def test_switch_strips_whitespace(monkeypatch):
    """Extra whitespace is stripped before delegating."""
    mixin = SwitchCommands()
    calls: list[str] = []
    monkeypatch.setattr(mixin, "_handle_agent_command", calls.append)

    mixin._handle_switch_command("  crs  ")

    assert calls == ["crs"]

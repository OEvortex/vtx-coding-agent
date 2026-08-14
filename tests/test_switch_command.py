"""Tests for the /switch slash command."""

from __future__ import annotations

from unittest.mock import MagicMock

from vtx.ui.commands.switch import SwitchCommands


def test_switch_delegates_to_agent_handler():
    """``/switch <name>`` delegates to ``_handle_agent_command``."""
    mixin = SwitchCommands()
    mixin._handle_agent_command = MagicMock()

    mixin._handle_switch_command("crs")

    mixin._handle_agent_command.assert_called_once_with("crs")


def test_switch_strips_whitespace():
    """Extra whitespace is stripped before delegating."""
    mixin = SwitchCommands()
    mixin._handle_agent_command = MagicMock()

    mixin._handle_switch_command("  crs  ")

    mixin._handle_agent_command.assert_called_once_with("crs")

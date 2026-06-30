from __future__ import annotations

import argparse
from unittest.mock import MagicMock, patch

import pytest

from vtx.ui.autocomplete import DEFAULT_COMMANDS, SlashCommand
from vtx.ui.selection_mode import SelectionMode

from vtx_claw.ui import CLAW_ACTIONS, CLAW_DESCRIPTIONS, ClawVtx, run_tui


@pytest.fixture
def claw_app(tmp_path, monkeypatch):
    """Return a configured ClawVtx app instance without running it."""
    monkeypatch.setenv("HOME", str(tmp_path))
    app = ClawVtx(
        cwd=str(tmp_path),
        model="gpt-4o-mini",
        provider="openai",
        auto_discover_extensions=False,
        auto_discover_agents=False,
    )
    return app


def test_claw_vtx_title():
    assert ClawVtx.TITLE == "vtx-claw"


def test_run_tui_registers_claw_slash_command():
    before = [c.name for c in DEFAULT_COMMANDS]
    if "claw" in before:
        # Remove any pre-existing claw command so we can observe registration.
        DEFAULT_COMMANDS[:] = [c for c in DEFAULT_COMMANDS if c.name != "claw"]

    args = argparse.Namespace(
        model=None,
        provider=None,
        api_key=None,
        base_url=None,
        resume_session=None,
        continue_recent=False,
        openai_compat_auth=None,
        anthropic_compat_auth=None,
        extension_paths=[],
        no_extensions=True,
        agent=None,
        agent_files=[],
        no_agents=True,
        goal=None,
    )

    with patch.object(ClawVtx, "run"):
        run_tui(args)

    assert any(c.name == "claw" for c in DEFAULT_COMMANDS)


def test_claw_handle_command_routes_claw(claw_app):
    chat = MagicMock()
    with patch.object(claw_app, "query_one", return_value=chat):
        handled = claw_app._handle_command("/claw help")

    assert handled is True
    chat.add_info_message.assert_called_once()
    text = chat.add_info_message.call_args[0][0]
    assert "Claw Commands Help" in text


def test_claw_handle_command_delegates_unknown(claw_app):
    """/unknown should fall through to the parent command router."""
    with patch.object(ClawVtx, "_handle_command", return_value=False) as parent:
        handled = claw_app._handle_command("/unknown")

    assert handled is False


@pytest.mark.parametrize("action", CLAW_ACTIONS)
def test_claw_execute_action_does_not_crash(claw_app, action, tmp_path, monkeypatch):
    chat = MagicMock()
    monkeypatch.setenv("HOME", str(tmp_path))

    with patch.object(claw_app, "query_one", return_value=chat):
        claw_app._execute_claw_action(action)

    chat.add_info_message.assert_called_once()


def test_claw_selection_mode_uses_enum(claw_app):
    assert SelectionMode.CLAW == "claw"

"""Tests for the /update slash command and self_update helper."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from vtx.coding_agent.self_update import _installer_choice
from vtx.tui.autocomplete import DEFAULT_COMMANDS
from vtx.tui.commands import CommandsMixin
from vtx.tui.commands.update import UpdateCommands


class FakeChat:
    def __init__(self):
        self.messages: list[tuple[str, bool]] = []

    def add_info_message(self, message: str, error: bool = False) -> None:
        self.messages.append((message, error))


class FakeApp(UpdateCommands):
    def __init__(self):
        self.chat = FakeChat()
        self.workers = []

    def query_one(self, selector: str, *args, **kwargs):
        if selector == "#chat-log":
            return self.chat
        raise ValueError(f"Unknown selector {selector}")

    def run_worker(self, coro, exclusive: bool = False):
        self.workers.append(coro)
        return MagicMock()


def test_update_in_default_commands():
    """Verify /update is registered in DEFAULT_COMMANDS autocomplete list."""
    commands = {cmd.name: cmd.description for cmd in DEFAULT_COMMANDS}
    assert "update" in commands
    assert "update" in commands["update"].lower() or "vtx" in commands["update"].lower()


def test_commands_mixin_dispatches_update(monkeypatch):
    """CommandsMixin routes /update to _handle_update_command."""
    app = CommandsMixin()
    called = False

    def fake_handle_update():
        nonlocal called
        called = True

    monkeypatch.setattr(app, "_handle_update_command", fake_handle_update)
    handled = app._handle_command("/update")
    assert handled is True
    assert called is True


def test_handle_update_command_initiates_worker(monkeypatch):
    """_handle_update_command posts progress message and schedules worker."""
    app = FakeApp()
    app._handle_update_command()

    assert len(app.chat.messages) == 1
    assert "Checking for updates" in app.chat.messages[0][0]
    assert len(app.workers) == 1
    for w in app.workers:
        w.close()


@pytest.mark.asyncio
async def test_do_update_success_already_up_to_date(monkeypatch):
    """_do_update displays already up to date notice when up to date."""
    app = FakeApp()
    monkeypatch.setattr(
        "vtx.tui.commands.update.self_update", lambda: (True, "Already up to date (uv tool).")
    )

    await app._do_update()

    assert len(app.chat.messages) == 1
    msg, err = app.chat.messages[0]
    assert "Already up to date (uv tool)." in msg
    assert err is False


@pytest.mark.asyncio
async def test_do_update_success_upgraded(monkeypatch):
    """_do_update prompts to restart when upgraded successfully."""
    app = FakeApp()
    monkeypatch.setattr(
        "vtx.tui.commands.update.self_update", lambda: (True, "Updated successfully via uv tool.")
    )

    await app._do_update()

    assert len(app.chat.messages) == 1
    msg, err = app.chat.messages[0]
    assert "Updated successfully via uv tool." in msg
    assert "restart vtx" in msg
    assert err is False


@pytest.mark.asyncio
async def test_do_update_failure(monkeypatch):
    """_do_update displays error message when self_update fails."""
    app = FakeApp()
    monkeypatch.setattr(
        "vtx.tui.commands.update.self_update", lambda: (False, "network unreachable")
    )

    await app._do_update()

    assert len(app.chat.messages) == 1
    msg, err = app.chat.messages[0]
    assert "Update failed: network unreachable" in msg
    assert err is True


def test_installer_choice_prefers_pip_with_env(monkeypatch):
    """VTX_UPDATE_USE_PIP forces pip."""
    monkeypatch.setenv("VTX_UPDATE_USE_PIP", "1")
    installer, cmd = _installer_choice()
    assert installer == "pip"
    assert "pip" in cmd


def test_installer_choice_detects_uv_tool(monkeypatch):
    """uv tool is chosen when uv and package are in uv tool list."""
    monkeypatch.delenv("VTX_UPDATE_USE_PIP", raising=False)
    monkeypatch.setattr("vtx.coding_agent.self_update._find_executable", lambda name: name == "uv")
    monkeypatch.setattr("vtx.coding_agent.self_update._is_uv_tool", lambda pkg: True)

    installer, cmd = _installer_choice()
    assert installer == "uv tool"
    assert cmd == ["uv", "tool", "upgrade", "vtx-coding-agent"]


def test_installer_choice_detects_pipx(monkeypatch):
    """pipx is chosen when pipx and package are in pipx list."""
    monkeypatch.delenv("VTX_UPDATE_USE_PIP", raising=False)
    monkeypatch.setattr(
        "vtx.coding_agent.self_update._find_executable", lambda name: name == "pipx"
    )
    monkeypatch.setattr("vtx.coding_agent.self_update._is_uv_tool", lambda pkg: False)
    monkeypatch.setattr("vtx.coding_agent.self_update._is_pipx_tool", lambda pkg: True)

    installer, cmd = _installer_choice()
    assert installer == "pipx"
    assert cmd == ["pipx", "upgrade", "vtx-coding-agent"]

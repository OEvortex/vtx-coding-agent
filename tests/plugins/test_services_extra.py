"""Tests for the MCP, goals, ui, and commands rows, and the TUI channel."""

from __future__ import annotations

import pytest

from vtx.plugins import boot
from vtx.plugins.builtins.commands import CommandService
from vtx.plugins.services import GoalService, MCPService
from vtx.plugins.tui import BUILTIN_COMMANDS, TUIChannel, install, uninstall

# --------------------------------------------------------------------------
# ctx.mcp
# --------------------------------------------------------------------------


def test_the_composition_provides_the_mcp_service():
    host = boot()
    try:
        assert "mcp" in host.base
    finally:
        host.dispose()


def test_expose_records_what_a_manager_handed_back():
    service = MCPService()
    accepted = service.expose(["tool-a", "tool-b"])
    assert accepted == ["tool-a", "tool-b"]
    assert service.tools() == ["tool-a", "tool-b"]


def test_expose_appends_across_calls():
    service = MCPService()
    service.expose(["tool-a"])
    service.expose(["tool-b"])
    assert service.tools() == ["tool-a", "tool-b"]


def test_the_mcp_rows_teardown_drops_the_exposed_tools():
    host = boot()
    try:
        host.base.mcp.expose(["tool-a"])
        assert host.base.mcp.tools() == ["tool-a"]
        host.unmount("mcp")
        # The row's teardown ran, and the service is gone with it.
        assert "mcp" not in host.base
    finally:
        host.dispose()


def test_a_manager_can_be_replaced():
    service = MCPService()
    first = object()
    second = object()
    service.set_manager(first)
    assert service.manager is first
    service.set_manager(second)
    assert service.manager is second


def test_connect_is_idempotent():
    service = MCPService()
    service.set_manager("already")
    assert service.connect(cwd="/tmp") == "already"


# --------------------------------------------------------------------------
# ctx.goals
# --------------------------------------------------------------------------


def test_a_goal_is_one_record_per_session():
    service = GoalService()
    service.set("s1", {"objective": "ship it"})
    assert service.get("s1") == {"objective": "ship it"}
    assert service.get("other") is None


def test_the_active_goal_follows_the_last_set():
    service = GoalService()
    service.set("s1", "first")
    service.set("s2", "second")
    assert service.active == "second"
    assert service.sessions() == ["s1", "s2"]


def test_clearing_the_active_session_leaves_no_active_goal():
    service = GoalService()
    service.set("s1", "only")
    assert service.clear("s1") is True
    assert service.active is None
    assert service.clear("s1") is False


def test_clearing_a_non_active_session_keeps_the_active_one():
    service = GoalService()
    service.set("s1", "first")
    service.set("s2", "second")
    service.clear("s1")
    assert service.active == "second"


# --------------------------------------------------------------------------
# ctx.ui
# --------------------------------------------------------------------------


def test_the_composition_provides_the_ui_and_command_rows():
    host = boot()
    try:
        assert "ui" in host.base
        assert "commands" in host.base
    finally:
        host.dispose()


def test_the_tui_channel_notifies_through_the_app():
    class FakeApp:
        def __init__(self):
            self.seen: list[tuple[str, bool]] = []

        def add_info_message(self, message: str, error: bool = False) -> None:
            self.seen.append((message, error))

    channel = TUIChannel(FakeApp())
    channel.notify("hello")
    assert channel._app.seen == [("hello", False)]


def test_the_tui_channel_routes_a_level_to_its_own_sink():
    class FakeApp:
        def __init__(self):
            self.errors: list[str] = []

        def add_error_message(self, message: str) -> None:
            self.errors.append(message)

    channel = TUIChannel(FakeApp())
    channel.notify("broke", "error")
    assert channel._app.errors == ["broke"]


def test_a_notify_failure_does_not_propagate():
    class BrokenApp:
        def add_info_message(self, message: str) -> None:
            raise RuntimeError("widget gone")

    channel = TUIChannel(BrokenApp())
    channel.notify("hello")  # must not raise


def test_a_question_without_an_app_returns_none():
    channel = TUIChannel(object())
    assert channel.ask("pick one") is None


def test_a_question_failure_returns_none():
    class BrokenApp:
        def ask_user(self, question, options=None):
            raise RuntimeError("no session")

    channel = TUIChannel(BrokenApp())
    assert channel.ask("pick") is None


def test_install_publishes_the_builtins_as_command_descriptors():
    host = boot()
    try:
        install(object(), host)
        commands = host.base.commands
        for name, description in BUILTIN_COMMANDS:
            assert name in commands, name
            assert commands.describe(name) == description
    finally:
        host.dispose()


def test_install_is_idempotent():
    host = boot()
    try:
        install(object(), host)
        first = dict(host.base.commands._commands)
        install(object(), host)
        assert dict(host.base.commands._commands) == first
    finally:
        host.dispose()


def test_uninstall_removes_only_the_descriptors_it_published():
    host = boot()
    try:
        host.base.commands.register("mine", lambda: None, "mine")
        install(object(), host)
        uninstall(host)
        assert "mine" in host.base.commands
        for name, _description in BUILTIN_COMMANDS:
            assert name not in host.base.commands
    finally:
        host.dispose()


def test_install_without_a_composition_is_a_no_op():
    install(object(), None)
    uninstall(None)


def test_every_builtin_descriptor_has_a_name_and_a_description():
    names = [name for name, _ in BUILTIN_COMMANDS]
    assert len(names) == len(set(names))
    for name, description in BUILTIN_COMMANDS:
        assert name and description


# --------------------------------------------------------------------------
# ctx.commands
# --------------------------------------------------------------------------


def test_a_command_without_a_model_turn():
    service = CommandService()
    service.register("ping", lambda: "pong", "reply pong")
    assert service.dispatch("ping") == "pong"
    assert service.describe("ping") == "reply pong"


def test_a_command_name_must_be_non_empty():
    service = CommandService()
    with pytest.raises(ValueError, match="non-empty"):
        service.register("", lambda: None)


def test_dispatching_an_unknown_command_names_it():
    service = CommandService()
    with pytest.raises(KeyError, match="nope"):
        service.dispatch("nope")


def test_re_registering_replaces_the_handler():
    service = CommandService()
    service.register("x", lambda: "first")
    service.register("x", lambda: "second")
    assert service.dispatch("x") == "second"


def test_arguments_and_keywords_reach_the_handler():
    service = CommandService()
    service.register("echo", lambda *args, **kwargs: (args, kwargs))
    assert service.dispatch("echo", 1, key="v") == ((1,), {"key": "v"})

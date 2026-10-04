"""The /effort panel: alias for /thinking, per-level descriptions, and that
ctrl+t keeps cycling alongside it.
"""

from __future__ import annotations

from typing import Any

import pytest

from vtx.tui.app import Vtx
from vtx.tui.autocomplete import DEFAULT_COMMANDS
from vtx.tui.commands.settings import THINKING_LEVEL_DESCRIPTIONS

LEVELS = ["low", "medium", "high", "xhigh", "max"]
_PROVIDER = object()


class FakeChat:
    def __init__(self) -> None:
        self.infos: list[str] = []

    def show_status(self, message: str) -> None:
        self.infos.append(message)

    def add_info_message(self, message: str, error: bool = False) -> None:
        self.infos.append(message)


class FakeRuntime:
    def __init__(self, levels: list[str] | None = None, provider: Any = _PROVIDER) -> None:
        self.provider = provider
        self.effective_thinking_levels = LEVELS if levels is None else levels
        self.thinking_level = "low"
        self.model = "stealth/space-bunny-alpha"
        self.model_provider = "kilo"


class FakeApp:
    action_cycle_thinking_level = Vtx.action_cycle_thinking_level

    def __init__(self, runtime: FakeRuntime) -> None:
        self._runtime = runtime
        self.chat = FakeChat()
        self.picker: list[Any] = []

    def query_one(self, selector: str, widget_type: Any = None) -> FakeChat:
        assert selector == "#chat-log"
        return self.chat

    def _show_selection_picker(self, items, selection_mode, **kwargs) -> None:
        self.picker = items
        self.selection_mode = selection_mode


def _app(levels: list[str] | None = None) -> FakeApp:
    return FakeApp(FakeRuntime(levels))


def _borrow(name: str):
    """Bind a real SettingsCommands method onto the fake."""
    from vtx.tui.commands.settings import SettingsCommands

    return getattr(SettingsCommands, name)


FakeApp._thinking_availability = _borrow("_thinking_availability")


def _handle(app: FakeApp, args: str = "") -> None:
    """Call the real handler, borrowed from SettingsCommands."""
    _borrow("_handle_thinking_command")(app, args)


def test_every_advertised_level_has_a_description():
    for level in LEVELS:
        assert level in THINKING_LEVEL_DESCRIPTIONS, level
        assert THINKING_LEVEL_DESCRIPTIONS[level].strip()


def test_panel_labels_each_level_with_its_description():
    app = _app()
    # Current level must be one the model does not offer, so no stray checkmark.
    app._runtime.thinking_level = "unlisted"
    _handle(app)
    assert [i.label for i in app.picker] == LEVELS
    assert [i.description for i in app.picker] == [
        THINKING_LEVEL_DESCRIPTIONS[lvl] for lvl in LEVELS
    ]


def test_panel_marks_the_current_level():
    app = _app()
    app._runtime.thinking_level = "high"
    _handle(app)
    marked = [i.label for i in app.picker if "✓" in i.label]
    assert marked == ["high ✓"]


def test_panel_names_the_model_and_the_available_count():
    """A bare list gave no way to tell a missing level from an unselected one."""
    app = _app()
    _handle(app)
    message = app.chat.infos[-1]
    assert "stealth/space-bunny-alpha" in message
    assert "5 available" in message
    assert "ctrl+t" in message


def test_panel_omits_levels_the_model_does_not_advertise():
    app = _app(["low", "high"])
    _handle(app)
    assert [i.label for i in app.picker] == ["low ✓", "high"]


def test_argument_selects_a_level_without_opening_the_panel():
    app = _app()
    selected: list[str] = []
    app._select_thinking_level = selected.append  # type: ignore[attr-defined]
    _handle(app, "high")
    assert selected == ["high"]
    assert app.picker == []


def test_unknown_argument_is_rejected_with_the_valid_set():
    app = _app()
    _handle(app, "nope")
    assert app.picker == []
    assert "Invalid thinking level: nope" in app.chat.infos[-1]
    assert "low, medium, high, xhigh, max" in app.chat.infos[-1]


def test_effort_is_deliberately_not_advertised_in_autocomplete():
    """These sub-commands stay hidden; /settings is the discoverable entry."""
    names = {c.name for c in DEFAULT_COMMANDS}
    assert "effort" not in names
    assert "thinking" not in names
    assert "settings" in names


@pytest.mark.parametrize("cmd", ["thinking", "effort"])
def test_both_commands_route_to_the_thinking_handler(cmd):
    """`/effort` is an alias, so both must reach the one handler.

    Asserted on the routing table source: a bytecode poke here would break on
    any refactor without telling us anything about behaviour.
    """
    import inspect

    from vtx.tui.commands import CommandsMixin

    src = inspect.getsource(CommandsMixin._handle_command)
    branch = src.split(f'cmd == "{cmd}"')[1]
    assert "_handle_thinking_command" in branch.split("return True")[0]


def test_panel_reports_when_the_catalog_entry_is_missing():
    """A mistyped id silently widened the offered set; say so instead.

    `get_model` matches exactly, so `space-bunny-alpha` misses the catalog
    entry `stealth/space-bunny-alpha` and falls back to the provider's raw
    enum, which offers levels the model marks unsupported.
    """
    app = _app()
    app._runtime.model = "space-bunny-alpha"  # not the catalog id

    _missing, reason = _borrow("_thinking_availability")(app)
    assert reason == "catalog entry not found, check the exact model id"

    _handle(app)
    assert "catalog entry not found" in app.chat.infos[-1]


def test_panel_names_levels_the_model_advertises_but_vtx_will_not_offer():
    app = _app()
    missing, reason = _borrow("_thinking_availability")(app)
    # The catalog is only loaded when the isolated test config allows it; this
    # test is about the panel wording, not about catalog availability.
    if reason or not missing:
        pytest.skip(f"catalog unavailable in this env: {reason or 'nothing withheld'}")

    _handle(app)
    assert "not offered: " in app.chat.infos[-1]

"""The thinking-level cycle must always report a change.

`ctrl+t` intermittently read as a dead key. `_select_thinking_level` applied
two widget updates and only then wrote the status line, with no error handling
on either update, so any failed lookup (an InfoBar not yet mounted, a
transient screen state) aborted the action before the user saw anything —
while the level had in fact already changed underneath.

The fix inverts the order: report first, update the widgets best-effort.
"""

import types

from vtx.coding_agent.tui.commands.settings import SettingsCommands


class _FakeChat:
    def __init__(self) -> None:
        self.statuses: list[str] = []

    def show_status(self, message: str) -> None:
        self.statuses.append(message)


class _FakeInfoBar:
    def __init__(self) -> None:
        self.levels: list[str] = []

    def set_thinking_level(self, level: str) -> None:
        self.levels.append(level)


class _Runtime:
    def __init__(self) -> None:
        self.provider = object()
        self.thinking_level = "low"
        self.set_calls: list[str] = []

    def set_thinking_level(self, level: str) -> None:
        self.set_calls.append(level)
        self.thinking_level = level


class _Box(SettingsCommands):
    def __init__(self) -> None:
        self._runtime = _Runtime()
        self.chat = _FakeChat()
        self.info_bar = _FakeInfoBar()
        self.style_calls: list[str] = []
        self.missing_widgets: set[str] = set()

    def _sync_runtime_state(self) -> None:
        return None

    def query_one(self, selector, *_args, **_kwargs):
        if selector in self.missing_widgets:
            raise LookupError(f"no widget for {selector}")
        if selector == "#chat-log":
            return self.chat
        if selector == "#compact-footer":
            return self.info_bar
        raise LookupError(selector)

    def _apply_thinking_level_style(self, level: str) -> None:
        if "style" in self.missing_widgets:
            raise LookupError("style not ready")
        self.style_calls.append(level)


def test_a_successful_cycle_reports_and_updates():
    box = _Box()

    box._select_thinking_level("high")

    assert box._runtime.set_calls == ["high"]
    assert box.chat.statuses == ["Thinking level changed to high"]
    assert box.info_bar.levels == ["high"]
    assert box.style_calls == ["high"]


def test_a_missing_info_bar_still_reports_the_change():
    # The status line is what proves the key worked, so it must not depend on
    # a secondary widget update succeeding.
    box = _Box()
    box.missing_widgets = {"#compact-footer"}

    box._select_thinking_level("high")

    assert box._runtime.set_calls == ["high"]
    assert box.chat.statuses == ["Thinking level changed to high"]


def test_a_failing_style_update_still_reports_the_change():
    box = _Box()
    box.missing_widgets = {"style"}

    box._select_thinking_level("medium")

    assert box._runtime.set_calls == ["medium"]
    assert box.chat.statuses == ["Thinking level changed to medium"]


def test_no_provider_is_a_silent_no_op():
    box = _Box()
    box._runtime.provider = None

    box._select_thinking_level("high")

    assert box._runtime.set_calls == []
    assert box.chat.statuses == []


def test_the_mixin_stays_importable_without_an_app():
    # Guard against the fix being implemented as something that needs
    # a live app: this is a plain method on a mixin.
    assert isinstance(types.SimpleNamespace(), object)
    assert callable(SettingsCommands._select_thinking_level)

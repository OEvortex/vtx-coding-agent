"""ctrl+t must always answer: cycle when it can, say so when it can't."""

from __future__ import annotations

from typing import Any

from vtx.tui.app import Vtx

_PROVIDER = object()


class FakeChat:
    def __init__(self) -> None:
        self.statuses: list[str] = []

    def show_status(self, message: str) -> None:
        self.statuses.append(message)


class FakeRuntime:
    def __init__(self, levels: list[str], level: str = "none", provider: Any = _PROVIDER) -> None:
        self.provider = provider
        self.effective_thinking_levels = levels
        self.thinking_level = level


class FakeApp:
    action_cycle_thinking_level = Vtx.action_cycle_thinking_level

    def __init__(self, runtime: FakeRuntime) -> None:
        self._runtime = runtime
        self.chat = FakeChat()
        self.selected: list[str] = []

    def query_one(self, selector: str, widget_type: Any = None) -> FakeChat:
        assert selector == "#chat-log"
        return self.chat

    def _select_thinking_level(self, level: str) -> None:
        self.selected.append(level)
        self._runtime.thinking_level = level


def test_cycles_through_every_offered_level():
    app = FakeApp(FakeRuntime(["low", "medium", "high"], level="low"))
    for _ in range(3):
        app.action_cycle_thinking_level()
    assert app.selected == ["medium", "high", "low"]


def test_level_outside_the_offered_set_lands_on_the_lowest_one():
    # "none" is not offered here (the catalog marks the off level unsupported),
    # so the first press must not skip the lowest level it does offer.
    app = FakeApp(FakeRuntime(["low", "medium", "high"], level="none"))
    app.action_cycle_thinking_level()
    assert app.selected == ["low"]


def test_single_offered_level_reports_instead_of_doing_nothing():
    app = FakeApp(FakeRuntime(["none"], level="none"))
    app.action_cycle_thinking_level()
    assert app.selected == []
    assert app.chat.statuses == ["Thinking level: none"]


def test_missing_provider_reports_instead_of_doing_nothing():
    app = FakeApp(FakeRuntime(["low", "high"], provider=None))
    app.action_cycle_thinking_level()
    assert app.selected == []
    assert app.chat.statuses == ["Thinking level unavailable: agent not initialized"]

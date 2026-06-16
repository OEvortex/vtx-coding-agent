"""Tests for the app's ask_user key handling.

Mirrors the structure of ``test_app_approval_keys.py``: a fake app
with the ask_user state populated, and a ``FakeKeyEvent`` that records
whether the key was consumed. We drive ``Vtx._handle_ask_user_key``
directly to exercise the key-dispatch logic without a live Textual app.
"""

from typing import cast

from textual import events

from vtx.permissions import AskUserOption, AskUserResponse
from vtx.ui.app import Vtx


class FakeKeyEvent:
    def __init__(self, key: str) -> None:
        self.key = key
        self.prevented = False
        self.stopped = False

    def prevent_default(self) -> None:
        self.prevented = True

    def stop(self) -> None:
        self.stopped = True


class FakeChat:
    def __init__(self) -> None:
        self.updates: list[tuple[str, int, set[str]]] = []
        self.hides: list[str] = []
        self.input_value: str = ""

    def update_ask_user_selection(self, tool_id: str, highlight: int, toggled) -> None:
        self.updates.append((tool_id, highlight, set(toggled)))

    def hide_ask_user(self, tool_id: str) -> None:
        self.hides.append(tool_id)

    def ask_user_input_value(self, tool_id: str) -> str:
        return self.input_value


class FakeFuture:
    def __init__(self) -> None:
        self._done = False
        self._result: AskUserResponse | None = None

    def done(self) -> bool:
        return self._done

    def set_result(self, result: AskUserResponse) -> None:
        self._done = True
        self._result = result

    def result(self) -> AskUserResponse | None:
        return self._result


def _make_app(
    options: list[AskUserOption], multi: bool = False, input_value: str = ""
) -> tuple[FakeFuture, "AskUserApp", FakeChat]:
    future = FakeFuture()
    chat = FakeChat()
    chat.input_value = input_value
    app = AskUserApp(future=future, options=options, multi=multi, chat=chat)
    return future, app, chat


class AskUserApp:
    """Stand-in for the Vtx app that exposes the ask_user state."""

    def __init__(
        self, future: FakeFuture, options: list[AskUserOption], multi: bool, chat: FakeChat
    ) -> None:
        self._ask_user_future = future
        self._ask_user_tool_id = "tool-1"
        self._ask_user_options = list(options)
        self._ask_user_multi = multi
        self._ask_user_highlight = 0
        self._ask_user_toggled = set()
        self.chat = chat
        self.cleared = False

    def query_one(self, selector, widget_type):
        assert selector == "#chat-log"
        return self.chat

    def _clear_ask_user_state(self) -> None:
        if self._ask_user_tool_id is not None:
            self.chat.hide_ask_user(self._ask_user_tool_id)
        self.cleared = True
        self._ask_user_future = None
        self._ask_user_tool_id = None
        self._ask_user_options = []
        self._ask_user_toggled = set()
        self._ask_user_highlight = 0

    # Mirror the Vtx methods that the key handler calls. We only
    # need to delegate to the chat; the block state is mocked.
    def _refresh_ask_user_visual(self) -> None:
        if self._ask_user_tool_id is None:
            return
        self.chat.update_ask_user_selection(
            self._ask_user_tool_id, self._ask_user_highlight, set(self._ask_user_toggled)
        )

    def _ask_user_move(self, delta: int) -> None:
        total = len(self._ask_user_options) + 1
        self._ask_user_highlight = (self._ask_user_highlight + delta) % total
        self._refresh_ask_user_visual()

    def _ask_user_toggle_highlight(self) -> None:
        idx = self._ask_user_highlight
        if idx >= len(self._ask_user_options):
            return
        label = self._ask_user_options[idx].label
        if label in self._ask_user_toggled:
            self._ask_user_toggled.discard(label)
        else:
            self._ask_user_toggled.add(label)
        self._refresh_ask_user_visual()

    def _ask_user_pick(self, index: int) -> None:
        if self._ask_user_multi:
            self._ask_user_highlight = index
            label = (
                self._ask_user_options[index].label
                if index < len(self._ask_user_options)
                else None
            )
            if label is not None:
                if label in self._ask_user_toggled:
                    self._ask_user_toggled.discard(label)
                else:
                    self._ask_user_toggled.add(label)
            self._refresh_ask_user_visual()
            return
        self._ask_user_highlight = index
        self._ask_user_submit_highlighted()

    def _ask_user_submit_highlighted(self) -> None:
        if self._ask_user_future is None or self._ask_user_future.done():
            return
        idx = self._ask_user_highlight
        if idx >= len(self._ask_user_options):
            text = self.chat.ask_user_input_value(self._ask_user_tool_id or "").strip()
            if not text:
                return
            self._ask_user_future.set_result(AskUserResponse(custom_text=text))
        elif self._ask_user_multi:
            if not self._ask_user_toggled:
                label = self._ask_user_options[idx].label
                self._ask_user_future.set_result(AskUserResponse(selections=(label,)))
            else:
                self._ask_user_future.set_result(
                    AskUserResponse(selections=tuple(sorted(self._ask_user_toggled)))
                )
        else:
            label = self._ask_user_options[idx].label
            self._ask_user_future.set_result(AskUserResponse(selections=(label,)))
        self._clear_ask_user_state()

    def _ask_user_cancel(self) -> None:
        if self._ask_user_future and not self._ask_user_future.done():
            self._ask_user_future.set_result(AskUserResponse())
        self._clear_ask_user_state()


# -----------------------------------------------------------------
# Single-select: number keys pick immediately
# -----------------------------------------------------------------


def test_single_select_number_pick_submits_immediately():
    future, app, chat = _make_app([AskUserOption(label="npm"), AskUserOption(label="pnpm")])

    event = FakeKeyEvent("1")
    Vtx._handle_ask_user_key(cast(Vtx, app), cast(events.Key, event))

    result = future.result()
    assert result is not None
    assert result.selections == ("npm",)
    assert event.prevented is True
    assert event.stopped is True
    assert app.cleared is True
    assert chat.hides == ["tool-1"]


def test_single_select_number_pick_last_option():
    future, app, _chat = _make_app([AskUserOption(label="npm"), AskUserOption(label="pnpm")])

    event = FakeKeyEvent("2")
    Vtx._handle_ask_user_key(cast(Vtx, app), cast(events.Key, event))

    result = future.result()
    assert result is not None
    assert result.selections == ("pnpm",)


def test_single_select_number_pick_other_prompts_for_input():
    future, app, _chat = _make_app([AskUserOption(label="npm")], input_value="yarn please")

    # 1 option + 1 Other = 2 rows, so [2] picks Other
    event = FakeKeyEvent("2")
    Vtx._handle_ask_user_key(cast(Vtx, app), cast(events.Key, event))

    result = future.result()
    assert result is not None
    assert result.custom_text == "yarn please"
    assert app.cleared is True


def test_single_select_number_pick_other_empty_input_ignored():
    future, app, _chat = _make_app([AskUserOption(label="npm")], input_value="")

    event = FakeKeyEvent("2")
    Vtx._handle_ask_user_key(cast(Vtx, app), cast(events.Key, event))

    # Empty custom text: future stays unresolved, state preserved.
    assert not future.done()
    assert app.cleared is False


# -----------------------------------------------------------------
# Arrow keys / enter
# -----------------------------------------------------------------


def test_arrow_keys_move_highlight():
    _future, app, chat = _make_app(
        [AskUserOption(label="a"), AskUserOption(label="b"), AskUserOption(label="c")]
    )

    down = FakeKeyEvent("down")
    Vtx._handle_ask_user_key(cast(Vtx, app), cast(events.Key, down))
    assert app._ask_user_highlight == 1
    assert chat.updates[-1] == ("tool-1", 1, set())

    up = FakeKeyEvent("up")
    Vtx._handle_ask_user_key(cast(Vtx, app), cast(events.Key, up))
    assert app._ask_user_highlight == 0
    assert chat.updates[-1] == ("tool-1", 0, set())


def test_arrow_keys_wrap_around():
    _future, app, _chat = _make_app([AskUserOption(label="a"), AskUserOption(label="b")])

    # From the last row (Other, idx 2), down wraps to 0
    app._ask_user_highlight = 2
    event = FakeKeyEvent("down")
    Vtx._handle_ask_user_key(cast(Vtx, app), cast(events.Key, event))
    assert app._ask_user_highlight == 0

    # From 0, up wraps to last
    event = FakeKeyEvent("up")
    Vtx._handle_ask_user_key(cast(Vtx, app), cast(events.Key, event))
    assert app._ask_user_highlight == 2


def test_enter_submits_highlighted():
    future, app, _chat = _make_app(
        [AskUserOption(label="a"), AskUserOption(label="b"), AskUserOption(label="c")]
    )

    app._ask_user_highlight = 1
    event = FakeKeyEvent("enter")
    Vtx._handle_ask_user_key(cast(Vtx, app), cast(events.Key, event))

    result = future.result()
    assert result is not None
    assert result.selections == ("b",)
    assert app.cleared is True


def test_enter_on_other_with_text_submits_custom():
    future, app, _chat = _make_app([AskUserOption(label="a")], input_value="my answer")

    app._ask_user_highlight = 1  # Other
    event = FakeKeyEvent("enter")
    Vtx._handle_ask_user_key(cast(Vtx, app), cast(events.Key, event))

    result = future.result()
    assert result is not None
    assert result.custom_text == "my answer"


def test_enter_on_other_with_empty_text_does_nothing():
    future, app, _chat = _make_app([AskUserOption(label="a")], input_value="")

    app._ask_user_highlight = 1  # Other
    event = FakeKeyEvent("enter")
    Vtx._handle_ask_user_key(cast(Vtx, app), cast(events.Key, event))

    assert not future.done()
    assert app.cleared is False


# -----------------------------------------------------------------
# Multi-select: space toggles, enter submits all
# -----------------------------------------------------------------


def test_multi_select_space_toggles():
    _future, _app, _chat = _make_app(
        [AskUserOption(label="a"), AskUserOption(label="b"), AskUserOption(label="c")], multi=True
    )

    event = FakeKeyEvent(" ")
    Vtx._handle_ask_user_key(cast(Vtx, _app), cast(events.Key, event))
    assert "a" in _app._ask_user_toggled

    _app._ask_user_highlight = 1
    event = FakeKeyEvent(" ")
    Vtx._handle_ask_user_key(cast(Vtx, _app), cast(events.Key, event))
    assert "a" in _app._ask_user_toggled
    assert "b" in _app._ask_user_toggled

    event = FakeKeyEvent(" ")
    Vtx._handle_ask_user_key(cast(Vtx, _app), cast(events.Key, event))
    assert "b" not in _app._ask_user_toggled


def test_multi_select_enter_submits_all_toggled():
    future, app, _chat = _make_app(
        [AskUserOption(label="a"), AskUserOption(label="b"), AskUserOption(label="c")], multi=True
    )

    app._ask_user_toggled = {"a", "c"}
    event = FakeKeyEvent("enter")
    Vtx._handle_ask_user_key(cast(Vtx, app), cast(events.Key, event))

    result = future.result()
    assert result is not None
    assert result.selections == ("a", "c")


def test_multi_select_enter_with_no_toggles_picks_highlighted():
    future, app, _chat = _make_app(
        [AskUserOption(label="a"), AskUserOption(label="b")], multi=True
    )

    app._ask_user_highlight = 1
    event = FakeKeyEvent("enter")
    Vtx._handle_ask_user_key(cast(Vtx, app), cast(events.Key, event))

    result = future.result()
    assert result is not None
    assert result.selections == ("b",)


# -----------------------------------------------------------------
# Cancel
# -----------------------------------------------------------------


def test_escape_cancels_with_empty_response():
    future, app, chat = _make_app([AskUserOption(label="a")])

    event = FakeKeyEvent("escape")
    Vtx._handle_ask_user_key(cast(Vtx, app), cast(events.Key, event))

    result = future.result()
    assert result is not None
    assert result.is_empty
    assert app.cleared is True
    assert chat.hides == ["tool-1"]


# -----------------------------------------------------------------
# Out-of-range keys
# -----------------------------------------------------------------


def test_out_of_range_number_does_not_consume():
    future, app, _chat = _make_app([AskUserOption(label="a"), AskUserOption(label="b")])

    # Only 1-3 are valid; 4 should not be consumed
    event = FakeKeyEvent("4")
    Vtx._handle_ask_user_key(cast(Vtx, app), cast(events.Key, event))
    assert not future.done()
    # The handler returns False to signal "not mine" so on_key falls
    # through to approval / default handling.
    assert event.prevented is False
    assert event.stopped is False

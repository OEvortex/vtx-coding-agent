"""Tests for the app's ask_user key handling.

Mirrors the structure of ``test_app_approval_keys.py``: a fake app
with the ask_user state populated, and a ``FakeKeyEvent`` that records
whether the key was consumed. We drive ``Vtx._handle_ask_user_key``
directly to exercise the dispatch between keys and the
:class:`AskUserDialog` state machine without a live Textual app.
"""

from typing import cast

from textual import events

from vtx.core import AskUserQuestion, AskUserResponse
from vtx.tui.app import Vtx
from vtx.tui.ask_user import AskUserDialog


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
        self.rerenders: list[str] = []
        self.hides: list[str] = []
        self.custom_value = ""
        self.cleared_custom: list[str] = []

    def ask_user_custom_value(self, tool_id: str) -> str:
        return self.custom_value

    def set_ask_user_custom_value(self, tool_id: str, value: str) -> None:
        self.custom_value = value
        self.cleared_custom.append(value)

    def rerender_ask_user(self, tool_id: str) -> None:
        self.rerenders.append(tool_id)

    def hide_ask_user(self, tool_id: str) -> None:
        self.hides.append(tool_id)


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


def _dialog(n_options: int = 2, multi: bool = False) -> AskUserDialog:
    from vtx.core import AskUserOption

    question = AskUserQuestion(
        question="Pick one?",
        header="H",
        options=[AskUserOption(label=f"o{i}") for i in range(n_options)],
        multi_select=multi,
    )
    return AskUserDialog([question])


def _make_app(dialog: AskUserDialog | None = None) -> tuple[FakeFuture, "AskUserApp", FakeChat]:
    future = FakeFuture()
    chat = FakeChat()
    app = AskUserApp(future=future, dialog=dialog or _dialog(), chat=chat)
    return future, app, chat


class AskUserApp:
    """Stand-in for the Vtx app exposing the ask_user state."""

    def __init__(self, future: FakeFuture, dialog: AskUserDialog, chat: FakeChat) -> None:
        self._ask_user_future = future  # type: ignore[assignment]
        self._ask_user_tool_id = "tool-1"
        self._ask_dialog = dialog
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
        self._ask_dialog = None

    def _resolve_ask_user_future(self, dialog: AskUserDialog) -> None:
        future = self._ask_user_future
        if future is not None and not future.done():
            future.set_result(AskUserResponse() if dialog.cancelled else dialog.build_response())
        self._clear_ask_user_state()


# -----------------------------------------------------------------
# Single-select: number keys and enter resolve the questionnaire
# -----------------------------------------------------------------


def test_number_key_commits_and_resolves():
    future, app, chat = _make_app()

    event = FakeKeyEvent("1")
    Vtx._handle_ask_user_key(cast(Vtx, app), cast(events.Key, event))

    result = future.result()
    assert result is not None
    assert result.answers[0].answer == "o0"
    assert event.prevented is True
    assert event.stopped is True
    assert app.cleared is True
    assert chat.hides == ["tool-1"]


def test_enter_on_other_with_text_builds_custom_answer():
    future, app, chat = _make_app()
    chat.custom_value = "my own words"

    app._ask_dialog.handle_key("down")
    app._ask_dialog.handle_key("down")  # onto Type something.
    event = FakeKeyEvent("enter")
    Vtx._handle_ask_user_key(cast(Vtx, app), cast(events.Key, event))

    result = future.result()
    assert result is not None
    assert result.answers[0].kind == "custom"
    assert result.answers[0].answer == "my own words"


def test_enter_on_other_empty_text_opens_input_not_resolve():
    future, app, _chat = _make_app()
    app._ask_dialog.handle_key("down")
    app._ask_dialog.handle_key("down")  # onto Type something.

    event = FakeKeyEvent("enter")
    Vtx._handle_ask_user_key(cast(Vtx, app), cast(events.Key, event))

    assert not future.done()
    assert app.cleared is False
    assert app._ask_dialog.input_mode is True


# -----------------------------------------------------------------
# Navigation / toggling keeps the dialog open
# -----------------------------------------------------------------


def test_arrow_moves_and_rerenders_without_resolving():
    _future, app, chat = _make_app()
    event = FakeKeyEvent("down")
    Vtx._handle_ask_user_key(cast(Vtx, app), cast(events.Key, event))
    assert app._ask_dialog.row_kind() == ("option", 1)
    assert chat.rerenders == ["tool-1"]
    assert not app._ask_user_future.done()


def test_space_toggles_multi_option():
    _future, app, _chat = _make_app(_dialog(multi=True))
    Vtx._handle_ask_user_key(cast(Vtx, app), cast(events.Key, FakeKeyEvent(" ")))
    assert 0 in app._ask_dialog.current_state().toggled
    assert not app._ask_user_future.done()


def test_ctrl_u_clears_draft_via_chat_setter():
    _future, app, chat = _make_app()
    chat.custom_value = "draft text"
    app._ask_dialog.current_state().draft = "draft text"

    Vtx._handle_ask_user_key(cast(Vtx, app), cast(events.Key, FakeKeyEvent("ctrl+u")))

    assert chat.cleared_custom == [""]
    assert app._ask_dialog.current_state().draft == ""


# -----------------------------------------------------------------
# Cancel
# -----------------------------------------------------------------


def test_escape_cancels_with_decline_response():
    future, app, chat = _make_app()

    event = FakeKeyEvent("escape")
    Vtx._handle_ask_user_key(cast(Vtx, app), cast(events.Key, event))

    result = future.result()
    assert result is not None
    assert result.is_empty
    assert app.cleared is True
    assert chat.hides == ["tool-1"]


# -----------------------------------------------------------------
# Non-consumed keys fall through
# -----------------------------------------------------------------


def test_out_of_range_number_does_not_consume():
    future, app, _chat = _make_app()

    event = FakeKeyEvent("7")
    Vtx._handle_ask_user_key(cast(Vtx, app), cast(events.Key, event))

    assert not future.done()
    assert event.prevented is False
    assert event.stopped is False


def test_unhandled_letter_does_not_consume():
    future, app, _chat = _make_app()

    event = FakeKeyEvent("z")
    Vtx._handle_ask_user_key(cast(Vtx, app), cast(events.Key, event))

    assert not future.done()
    assert event.prevented is False

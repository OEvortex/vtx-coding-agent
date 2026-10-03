"""Tab must accept the highlighted completion in the slash/at dropdowns.

Tab is the key every other shell uses to accept a completion, but it only moved
the highlight here: ``/`` and ``@`` dropdowns accepted on Enter and not on Tab,
so a user reaching for Tab to accept fell through to path completion instead.
"""

import types

from vtx.tui.input import InputBox


class _FakeSelection:
    def __init__(self, start, end):
        self.start = start
        self.end = end


class _FakeTextArea:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.selection = _FakeSelection(0, len(text))
        self.inserted: list[str] = []

    def insert(self, text: str) -> None:
        self.inserted.append(text)
        col = self.selection.end
        self.text = self.text[:col] + text + self.text[col:]
        self.selection = _FakeSelection(0, col + len(text))


class _TestableInputBox(InputBox):
    def __init__(self) -> None:
        super().__init__(cwd="/tmp")
        self._fake_textarea = _FakeTextArea("")
        self.messages: list[object] = []
        self.worker_started = False

    @property
    def app(self):
        # action_submit reads app state (ask/approval futures, queue editing)
        # that does not exist outside a running app.
        return types.SimpleNamespace(
            _ask_user_future=None,
            _approval_future=None,
            start_queue_edit=lambda: False,
            finish_queue_edit=lambda *_a: False,
        )

    def query_one(self, *args, **kwargs):
        return self._fake_textarea

    def post_message(self, *args, **kwargs):
        self.messages.append(args[0])

    def run_worker(self, *args, **kwargs):
        self.worker_started = True


def test_tab_accepts_the_highlighted_completion():
    box = _TestableInputBox()
    box._is_completing = True

    box.action_tab_complete()

    assert [type(m) for m in box.messages] == [InputBox.CompletionSelect]
    # Accepting must not fall through to path completion.
    assert box.worker_started is False


def test_tab_falls_back_to_path_completion_when_no_dropdown_is_open():
    box = _TestableInputBox()
    box._is_completing = False

    box.action_tab_complete()

    assert box.messages == []
    assert box.worker_started is True


def test_enter_still_submits_when_a_completion_is_open():
    # Regression guard: Tab taking over the accept must not change Enter.
    box = _TestableInputBox()
    box._is_completing = True

    box.action_submit()

    assert [type(m) for m in box.messages] == [InputBox.CompletionSelect]

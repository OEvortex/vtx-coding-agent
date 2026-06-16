"""Tests for InputBox action methods when ask_user is active.

The InputBox has BINDINGS with ``priority=True`` for ``enter``,
``up``, ``down``, and ``escape``. These bindings fire their action
methods (``action_submit``, ``action_cursor_up``,
``action_cursor_down``, ``action_cancel``) BEFORE the chat input's
``_on_key`` has a chance to run. So when ask_user is active, the
actions themselves need to forward picker keys to the app's on_key —
otherwise the bindings consume them and the picker never sees them.
"""

from typing import Any

from vtx.permissions import AskUserOption
from vtx.ui.input import _is_ask_user_picker_key


class FakeFuture:
    def __init__(self) -> None:
        self._done = False
        self._result: Any = None

    def done(self) -> bool:
        return self._done

    def set_result(self, result: Any) -> None:
        self._done = True
        self._result = result

    def result(self) -> Any:
        return self._result


class FakeApp:
    def __init__(self) -> None:
        self._approval_future = None
        self._ask_user_future: FakeFuture | None = None
        self._ask_user_options: list[AskUserOption] = []
        self._ask_user_multi: bool = False
        self._ask_user_highlight: int = 0
        self._ask_user_toggled: set[str] = set()
        self.forwarded_keys: list[str] = []
        self.cleared = False

    def on_key(self, event: Any) -> None:
        # Mirror the real app's on_key: route to ask_user first.
        self.forwarded_keys.append(event.key)
        if (
            self._ask_user_future is not None
            and not self._ask_user_future.done()
            and self._handle_ask_user_key(event)
        ):
            return

    def _handle_ask_user_key(self, event: Any) -> bool:
        """Minimal replica of the real Vtx._handle_ask_user_key."""
        if self._ask_user_future is None or self._ask_user_future.done():
            return False
        from vtx.permissions import AskUserResponse

        key = event.key
        if key.isdigit() and len(key) == 1:
            n = int(key)
            total = len(self._ask_user_options) + 1
            if 1 <= n <= total:
                self._ask_user_pick(n - 1)
                return True
            return False
        if key in ("up", "k"):
            self._ask_user_highlight = (self._ask_user_highlight - 1) % (
                len(self._ask_user_options) + 1
            )
            return True
        if key in ("down", "j"):
            self._ask_user_highlight = (self._ask_user_highlight + 1) % (
                len(self._ask_user_options) + 1
            )
            return True
        if key == "enter":
            self._ask_user_submit_highlighted()
            return True
        if key == "escape":
            self._ask_user_future.set_result(AskUserResponse())
            self.cleared = True
            return True
        return False

    def _ask_user_pick(self, index: int) -> None:
        from vtx.permissions import AskUserResponse

        self._ask_user_highlight = index
        if index < len(self._ask_user_options):
            label = self._ask_user_options[index].label
            assert self._ask_user_future is not None
            self._ask_user_future.set_result(AskUserResponse(selections=(label,)))
        # else: Other row — would read input, no fake here
        self.cleared = True

    def _ask_user_submit_highlighted(self) -> None:
        from vtx.permissions import AskUserResponse

        idx = self._ask_user_highlight
        if idx < len(self._ask_user_options):
            label = self._ask_user_options[idx].label
            assert self._ask_user_future is not None
            self._ask_user_future.set_result(AskUserResponse(selections=(label,)))
        self.cleared = True


# -----------------------------------------------------------------
# Replicas of the InputBox action methods, scoped to the ask_user
# check we added. These tests verify the forwarding logic without
# needing a full Textual app.
# -----------------------------------------------------------------


def _ask_user_guard(app: FakeApp, key: str) -> bool:
    """Return True if the key was forwarded; False to fall through."""
    ask_future = getattr(app, "_ask_user_future", None)
    if ask_future and not ask_future.done() and _is_ask_user_picker_key(key):
        app_on_key = getattr(app, "on_key", None)
        if callable(app_on_key):
            from textual import events

            app_on_key(events.Key(key, key))
            return True
    return False


def test_guard_forwards_digit_when_ask_user_active() -> None:
    app = FakeApp()
    app._ask_user_future = FakeFuture()
    assert _ask_user_guard(app, "1") is True
    assert app.forwarded_keys == ["1"]


def test_guard_forwards_up_when_ask_user_active() -> None:
    app = FakeApp()
    app._ask_user_future = FakeFuture()
    assert _ask_user_guard(app, "up") is True
    assert app.forwarded_keys == ["up"]


def test_guard_forwards_down_when_ask_user_active() -> None:
    app = FakeApp()
    app._ask_user_future = FakeFuture()
    assert _ask_user_guard(app, "down") is True
    assert app.forwarded_keys == ["down"]


def test_guard_forwards_enter_when_ask_user_active() -> None:
    app = FakeApp()
    app._ask_user_future = FakeFuture()
    assert _ask_user_guard(app, "enter") is True
    assert app.forwarded_keys == ["enter"]


def test_guard_forwards_escape_when_ask_user_active() -> None:
    app = FakeApp()
    app._ask_user_future = FakeFuture()
    assert _ask_user_guard(app, "escape") is True
    assert app.forwarded_keys == ["escape"]


def test_guard_forwards_j_k_when_ask_user_active() -> None:
    app = FakeApp()
    app._ask_user_future = FakeFuture()
    assert _ask_user_guard(app, "j") is True
    assert _ask_user_guard(app, "k") is True
    assert app.forwarded_keys == ["j", "k"]


def test_guard_forwards_space_when_ask_user_active() -> None:
    app = FakeApp()
    app._ask_user_future = FakeFuture()
    assert _ask_user_guard(app, "space") is True
    assert app.forwarded_keys == ["space"]


def test_guard_does_not_forward_when_ask_user_inactive() -> None:
    app = FakeApp()
    # No ask_user future
    assert _ask_user_guard(app, "enter") is False
    assert _ask_user_guard(app, "up") is False
    assert _ask_user_guard(app, "down") is False
    assert _ask_user_guard(app, "escape") is False
    assert app.forwarded_keys == []


def test_guard_does_not_forward_when_ask_user_done() -> None:
    app = FakeApp()
    future = FakeFuture()
    future.set_result(object())
    app._ask_user_future = future
    assert _ask_user_guard(app, "enter") is False
    assert _ask_user_guard(app, "up") is False
    assert app.forwarded_keys == []


def test_guard_does_not_forward_non_picker_key() -> None:
    """Letters and other non-picker keys should fall through, not
    be forwarded (the user is typing)."""
    app = FakeApp()
    app._ask_user_future = FakeFuture()
    for k in ("a", "tab", "ctrl+c", "f1"):
        assert _ask_user_guard(app, k) is False, k
    assert app.forwarded_keys == []


# -----------------------------------------------------------------
# End-to-end: simulate the actual action flow
# -----------------------------------------------------------------


def test_action_submit_forwards_when_ask_user_active() -> None:
    """Simulates action_submit: when ask_user is active, enter is
    forwarded to the app instead of submitting text."""
    app = FakeApp()
    ask_future = FakeFuture()
    app._ask_user_future = ask_future
    app._ask_user_options = [AskUserOption(label="npm"), AskUserOption(label="pnpm")]

    # Replica of action_submit's ask_user branch
    forwarded = _ask_user_guard(app, "enter")

    assert forwarded is True
    assert ask_future.done() is True
    assert ask_future.result().selections == ("npm",)  # first option


def test_action_cursor_up_forwards_when_ask_user_active() -> None:
    """Simulates action_cursor_up: when ask_user is active, up is
    forwarded to the app instead of browsing prompt history."""
    app = FakeApp()
    ask_future = FakeFuture()
    app._ask_user_future = ask_future
    app._ask_user_options = [
        AskUserOption(label="a"),
        AskUserOption(label="b"),
        AskUserOption(label="c"),
    ]

    forwarded = _ask_user_guard(app, "up")

    assert forwarded is True
    assert app.forwarded_keys == ["up"]


def test_action_cursor_down_forwards_when_ask_user_active() -> None:
    app = FakeApp()
    ask_future = FakeFuture()
    app._ask_user_future = ask_future
    app._ask_user_options = [AskUserOption(label="a")]

    forwarded = _ask_user_guard(app, "down")

    assert forwarded is True
    assert app.forwarded_keys == ["down"]


def test_action_cancel_forwards_when_ask_user_active() -> None:
    app = FakeApp()
    ask_future = FakeFuture()
    app._ask_user_future = ask_future

    forwarded = _ask_user_guard(app, "escape")

    assert forwarded is True
    assert ask_future.done() is True
    assert ask_future.result().is_empty

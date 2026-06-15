import inspect
from types import SimpleNamespace
from typing import cast

from vtx.ui.app import Vtx
from vtx.ui.blocks import HandoffLinkBlock


class _FakeChat:
    def __init__(self) -> None:
        self.statuses: list[str] = []

    def show_status(self, message: str) -> None:
        self.statuses.append(message)


class _FakeEvent:
    def __init__(self, target_session_id: str) -> None:
        self.target_session_id = target_session_id
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


def _fake_link_event(target_session_id: str) -> tuple[HandoffLinkBlock.LinkSelected, _FakeEvent]:
    event = _FakeEvent(target_session_id)
    return cast(HandoffLinkBlock.LinkSelected, event), event


class _TestVtx(Vtx):
    def __init__(self) -> None:
        super().__init__(cwd="/tmp")
        self._chat = _FakeChat()
        self.worker_calls: list[tuple[object, bool]] = []

    def query_one(self, *args, **kwargs):
        selector = args[0] if args else kwargs.get("selector")
        if selector == "#chat-log":
            return self._chat
        raise AssertionError(selector)

    def run_worker(self, *args, **kwargs):
        # Textual's run_worker has many overloads; the test only needs to capture
        # the work and the exclusive flag.
        work = args[0] if args else kwargs.get("work")
        exclusive = kwargs.get("exclusive", False) or (args[6] if len(args) > 6 else False)
        self.worker_calls.append((work, exclusive))
        return SimpleNamespace()


def test_handoff_link_interrupts_before_switch_when_running() -> None:
    app = _TestVtx()
    app._is_running = True
    app._cancel_event = None
    event, fake_event = _fake_link_event("session-123")

    app.on_handoff_link_selected(event)

    assert fake_event.stopped is True
    assert app._pending_session_switch_id == "session-123"
    assert app._interrupt_requested is True
    assert app._chat.statuses[-1] == "Interrupting before handoff..."
    assert app.worker_calls == []


def test_handoff_link_switches_immediately_when_idle() -> None:
    app = _TestVtx()
    event, fake_event = _fake_link_event("session-456")

    app.on_handoff_link_selected(event)

    assert fake_event.stopped is True
    assert app._pending_session_switch_id is None
    assert len(app.worker_calls) == 1
    coro, exclusive = app.worker_calls[0]
    assert inspect.iscoroutine(coro)
    coro.close()
    assert exclusive is True

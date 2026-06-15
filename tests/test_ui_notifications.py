from vtx import get_config
from vtx.core.types import StopReason
from vtx.events import AgentEndEvent, ToolApprovalEvent, TurnStartEvent
from vtx.ui.app import Vtx


def _make_app() -> Vtx:
    return Vtx(cwd=".")


def test_should_notify_for_event_respects_config() -> None:
    app = _make_app()
    get_config()._parsed.notifications.enabled = False

    assert app._should_notify_for_event(AgentEndEvent(stop_reason=StopReason.STOP)) is False
    assert app._should_notify_for_event(ToolApprovalEvent(future=None)) is False


def test_should_notify_for_event_notifies_for_configured_events() -> None:
    app = _make_app()
    get_config()._parsed.notifications.enabled = True

    assert app._should_notify_for_event(AgentEndEvent(stop_reason=StopReason.STOP)) is True
    assert app._should_notify_for_event(AgentEndEvent(stop_reason=StopReason.ERROR)) is True
    assert app._should_notify_for_event(ToolApprovalEvent(future=None)) is True
    assert app._should_notify_for_event(TurnStartEvent(turn=1)) is False


def test_should_notify_for_event_skips_interrupted_agent_end() -> None:
    app = _make_app()
    get_config()._parsed.notifications.enabled = True

    assert app._should_notify_for_event(AgentEndEvent(stop_reason=StopReason.INTERRUPTED)) is False


def test_notification_event_type_maps_events() -> None:
    app = _make_app()
    get_config()._parsed.notifications.enabled = True

    assert app._notification_event_type(AgentEndEvent(stop_reason=StopReason.STOP)) == "completion"
    assert app._notification_event_type(AgentEndEvent(stop_reason=StopReason.ERROR)) == "error"
    assert app._notification_event_type(ToolApprovalEvent(future=None)) == "permission"
    assert app._notification_event_type(AgentEndEvent(stop_reason=StopReason.INTERRUPTED)) is None

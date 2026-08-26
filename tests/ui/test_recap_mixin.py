"""Regression tests for RecapMixin._build_session_recap_context.

UserMessage.content can hold ImageContent parts alongside TextContent;
the context builder must skip non-text parts instead of crashing.
"""

from types import SimpleNamespace

from vtx.ai.agent.session import Session
from vtx.core.types import ImageContent, TextContent, UserMessage
from vtx.tui.recap import RecapMixin


def _host_with_session(session: Session):
    host = object.__new__(RecapMixin)
    host._runtime = SimpleNamespace(session=session)
    return host


def test_initial_task_skips_image_parts() -> None:
    session = Session.in_memory(cwd="/tmp")
    session.append_message(
        UserMessage(
            content=[
                TextContent(text="discribe this image"),
                ImageContent(data="aGk=", mime_type="image/png"),
            ]
        )
    )

    built = RecapMixin._build_session_recap_context(_host_with_session(session))

    assert built is not None
    _, key = built
    assert "discribe this image" in key


def test_image_only_first_message_does_not_crash() -> None:
    session = Session.in_memory(cwd="/tmp")
    session.append_message(
        UserMessage(content=[ImageContent(data="aGk=", mime_type="image/jpeg")])
    )
    session.append_message(UserMessage(content="follow up question"))

    built = RecapMixin._build_session_recap_context(_host_with_session(session))

    assert built is not None

"""A background sub-agent's result has to reach the model, unprompted.

The bug this pins: a ``background: true`` dispatch returns immediately, and its
completion was drained only *between* turns. When the sub-agent outlived the
parent's turn — the normal case, since a sub-agent takes minutes and a turn
takes seconds — nothing delivered the result. The record sat there until the
user happened to type, and meanwhile the model answered as if the work had
never happened.

The loop's half of the fix: whatever settled while the session was idle is
injected *before the first model call* of the next run, not one turn later.
(The TUI half — resuming the session at all — lives in
``tests/ui/test_background_wakeup.py``.)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vtx.agent.background import BackgroundTaskManager
from vtx.agent.loop import Agent
from vtx.agent.session import Session
from vtx.ai.providers.mock import MockProvider
from vtx.core import BackgroundTaskCompletedEvent

#: The tag the loop wraps a delivered result in.
NOTIFICATION_TAG = "vtx:background-task-completion"

FINDING = "RATED 7.0/10 — security risks around unrestricted file reads."


class _Result:
    def __init__(self, text: str) -> None:
        self.final_text = text
        self.turns = 3
        self.usage = type("U", (), {"total_tokens": 1234})()
        self.error: str | None = None
        self.stop_reason = type("S", (), {"value": "stop"})()


class _RecordingProvider(MockProvider):
    """A text-only mock that remembers the messages of every model call."""

    def __init__(self) -> None:
        super().__init__(scenario="simple_text")
        self.calls: list[list] = []

    async def _stream_impl(self, messages, **kwargs):  # type: ignore[override]
        self.calls.append(list(messages))
        return await super()._stream_impl(messages, **kwargs)


def _text_of(message) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.text for part in content if isinstance(getattr(part, "text", None), str)
        )
    return str(content)


async def _settled_task(manager: BackgroundTaskManager, tmp_path: Path) -> str:
    """Register a task, wait for it, and return its id."""

    async def _factory() -> _Result:
        return _Result(FINDING)

    record = await manager.register(
        description="Rate the VTX codebase",
        prompt="rate it",
        subagent_type="subagent",
        model=None,
        parent_session_id=None,
        run_coro_factory=_factory,
    )
    await manager.wait(record.task_id, timeout=2, cancel_event=None)
    return record.task_id


@pytest.mark.asyncio
async def test_settled_result_is_injected_before_the_first_model_call(tmp_path: Path) -> None:
    manager = BackgroundTaskManager(store_dir=tmp_path)
    await _settled_task(manager, tmp_path)

    provider = _RecordingProvider()
    agent = Agent(provider, [], Session.in_memory(), background_manager=manager)
    async for _ in agent.run("now do the rest"):
        pass

    # The *first* model call already carries the result. Before the fix the
    # completion was appended after this turn, so the model answered blind.
    first_call = _text_of_all(provider.calls[0])
    assert NOTIFICATION_TAG in first_call
    assert FINDING in first_call
    assert "Rate the VTX codebase" in first_call

    await manager.close()


@pytest.mark.asyncio
async def test_completion_event_reaches_the_ui(tmp_path: Path) -> None:
    manager = BackgroundTaskManager(store_dir=tmp_path)
    task_id = await _settled_task(manager, tmp_path)

    provider = _RecordingProvider()
    agent = Agent(provider, [], Session.in_memory(), background_manager=manager)
    events = [event async for event in agent.run("carry on")]

    completions = [e for e in events if isinstance(e, BackgroundTaskCompletedEvent)]
    assert [e.task_id for e in completions] == [task_id]
    assert completions[0].status == "completed"
    assert completions[0].turns == 3

    await manager.close()


@pytest.mark.asyncio
async def test_a_result_is_delivered_exactly_once(tmp_path: Path) -> None:
    """The completion is appended once; the record is never re-announced.

    It stays in the transcript afterwards — that is the point of a session —
    so the assertion is on the injected messages, not on later model calls.
    """
    manager = BackgroundTaskManager(store_dir=tmp_path)
    await _settled_task(manager, tmp_path)

    provider = _RecordingProvider()
    session = Session.in_memory()
    agent = Agent(provider, [], session, background_manager=manager)
    async for _ in agent.run("first"):
        pass
    async for _ in agent.run("second"):
        pass

    injected = [m for m in session.messages if NOTIFICATION_TAG in _text_of(m)]
    assert len(injected) == 1, f"injected {len(injected)} completion messages"
    # The record is marked notified, so a later run cannot repeat it.
    assert manager.drain_completed() == []

    await manager.close()


def _text_of_all(messages) -> str:
    return "\n".join(_text_of(message) for message in messages)

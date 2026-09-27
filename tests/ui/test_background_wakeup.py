"""The TUI half of background delivery: a settled sub-agent wakes the session.

The loop injects the result before the next run's first model call (see
``tests/tools/test_background_delivery.py``), but that only helps if a next
run happens. When a sub-agent finishes after its parent turn has ended — the
normal case, since sub-agents take minutes and turns take seconds — nothing
was starting one, so the result sat unread until the user typed something. The
model then answered as if the work had never happened.
"""

from __future__ import annotations

import pytest

from vtx.tui.agent_runner import _BACKGROUND_WAKEUP_PROMPT, MAX_BACKGROUND_WAKEUPS
from vtx.tui.app import Vtx
from vtx.tui.chat import ChatLog
from vtx.tui.goal_agents import REGISTRY


@pytest.fixture
def clean_registry():
    REGISTRY.clear()
    yield
    REGISTRY.clear()


class _Record:
    """The fields ``_on_background_task_settled`` reads off a task record."""

    def __init__(self, status: str = "completed", description: str = "Rate the VTX codebase"):
        self.status = status
        self.description = description
        self.parent_session_id = None


@pytest.mark.asyncio
async def test_settled_subagent_resumes_the_session(tmp_path, monkeypatch) -> None:
    app = Vtx(cwd=str(tmp_path))
    started: list[str] = []

    async def _fake_run(prompt: str, images=None) -> None:
        started.append(prompt)

    monkeypatch.setattr(app, "_run_agent", _fake_run)

    async with app.run_test(size=(100, 30)) as pilot:
        app._on_background_task_settled(_Record())
        await pilot.pause()

        assert started == [_BACKGROUND_WAKEUP_PROMPT]
        # And the user can see why the session started moving on its own.
        chat = app.query_one("#chat-log", ChatLog)
        assert any(
            "Rate the VTX codebase" in str(getattr(child, "content", ""))
            for child in chat.children
        )


@pytest.mark.asyncio
async def test_wakeup_is_silent_while_a_turn_is_already_running(tmp_path) -> None:
    app = Vtx(cwd=str(tmp_path))
    started: list[str] = []

    async def _fake_run(prompt: str, images=None) -> None:
        started.append(prompt)

    app._run_agent = _fake_run  # type: ignore[method-assign]
    app._is_running = True

    async with app.run_test(size=(100, 30)) as pilot:
        app._on_background_task_settled(_Record())
        await pilot.pause()
        # The loop drains between turns and delivers it; interrupting a live
        # turn is not this listener's job.
        assert started == []


@pytest.mark.asyncio
async def test_cancelled_task_does_not_wake_the_session(tmp_path) -> None:
    app = Vtx(cwd=str(tmp_path))
    started: list[str] = []

    async def _fake_run(prompt: str, images=None) -> None:
        started.append(prompt)

    app._run_agent = _fake_run  # type: ignore[method-assign]

    async with app.run_test(size=(100, 30)) as pilot:
        app._on_background_task_settled(_Record(status="cancelled"))
        await pilot.pause()
        assert started == []


@pytest.mark.asyncio
async def test_a_failed_subagent_still_wakes_the_session(tmp_path) -> None:
    app = Vtx(cwd=str(tmp_path))
    started: list[str] = []

    async def _fake_run(prompt: str, images=None) -> None:
        started.append(prompt)

    app._run_agent = _fake_run  # type: ignore[method-assign]

    async with app.run_test(size=(100, 30)) as pilot:
        app._on_background_task_settled(_Record(status="error", description="Audit the deps"))
        await pilot.pause()
        assert started == [_BACKGROUND_WAKEUP_PROMPT]

        chat = app.query_one("#chat-log", ChatLog)
        assert any(
            "Audit the deps" in str(getattr(child, "content", "")) for child in chat.children
        )


@pytest.mark.asyncio
async def test_wakeups_stop_chaining_and_say_so(tmp_path) -> None:
    """A wake-up turn that dispatches again must not cascade forever."""
    app = Vtx(cwd=str(tmp_path))
    started: list[str] = []

    async def _fake_run(prompt: str, images=None) -> None:
        started.append(prompt)

    app._run_agent = _fake_run  # type: ignore[method-assign]

    async with app.run_test(size=(100, 30)) as pilot:
        for index in range(MAX_BACKGROUND_WAKEUPS):
            app._on_background_task_settled(_Record(description=f"task {index}"))
            # Let the worker finish so the next completion is not ignored.
            await pilot.pause()
            app._bg_wakeup_busy = False
        assert len(started) == MAX_BACKGROUND_WAKEUPS

        app._on_background_task_settled(_Record(description="one too many"))
        await pilot.pause()
        assert len(started) == MAX_BACKGROUND_WAKEUPS

        chat = app.query_one("#chat-log", ChatLog)
        assert any(
            "auto-resuming" in str(getattr(child, "content", "")) for child in chat.children
        ), "the user must be told why the session stopped resuming itself"

        # A fresh prompt re-opens the budget.
        app._bg_wakeup_chain = 0
        app._on_background_task_settled(_Record(description="after a new prompt"))
        await pilot.pause()
        assert len(started) == MAX_BACKGROUND_WAKEUPS + 1


@pytest.mark.asyncio
async def test_watching_subscribes_once_and_unsubscribes(tmp_path) -> None:
    app = Vtx(cwd=str(tmp_path))
    async with app.run_test(size=(100, 30)):
        app.watch_background_tasks()
        first = app._bg_unsubscribe
        assert first is not None

        app.watch_background_tasks()
        assert app._bg_unsubscribe is first, "a second subscribe must not double up"

        app.unwatch_background_tasks()
        assert app._bg_unsubscribe is None
        app.unwatch_background_tasks()  # idempotent

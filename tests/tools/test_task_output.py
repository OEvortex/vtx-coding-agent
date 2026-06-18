"""Tests for the TaskOutput tool — companion retrieval path for
background sub-agents dispatched by the Task tool with ``background: true``.

Mirrors the surface contract of Claude Code's TaskOutput tool.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pydantic import ValidationError

from vtx.tools.background import BackgroundTaskManager, reset_manager, set_manager
from vtx.tools.task_output import TaskOutputParams, TaskOutputTool

# Module-level marker so the async tests in TestTaskOutputExecute are
# picked up by pytest-asyncio. The sync tests in TestParams are
# unaffected.
pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _install_manager(tmp_path: Path, monkeypatch):
    """Install a fresh manager before each test and clean up after."""
    mgr = BackgroundTaskManager(store_dir=tmp_path)
    token = set_manager(mgr)
    yield mgr
    reset_manager(token)
    asyncio.run(mgr.close())


class TestParams:
    def test_minimal(self):
        p = TaskOutputParams(task_id="bg_0001_abc")
        assert p.block is True
        assert p.timeout == 300.0

    def test_block_false(self):
        p = TaskOutputParams(task_id="bg_0001_abc", block=False)
        assert p.block is False

    def test_timeout_must_be_positive(self):
        with pytest.raises(ValidationError):
            TaskOutputParams(task_id="x", timeout=-1.0)

    def test_timeout_must_be_bounded(self):
        with pytest.raises(ValidationError):
            TaskOutputParams(task_id="x", timeout=4000.0)

    def test_task_id_required(self):
        with pytest.raises(ValidationError):
            TaskOutputParams(task_id="")


class _FakeFactory:
    def __init__(self, result=None, sleep: float = 0.0):
        self.result = result
        self.sleep = sleep

    async def __call__(self):
        if self.sleep:
            await asyncio.sleep(self.sleep)
        return self.result


class TestTaskOutputExecute:
    async def test_unknown_task_id(self, _install_manager: BackgroundTaskManager):
        tool = TaskOutputTool()
        res = await tool.execute(TaskOutputParams(task_id="bg_doesnotexist"))
        assert res.success is False
        assert "Unknown task_id" in (res.result or "")

    async def test_block_false_returns_running_status(
        self, _install_manager: BackgroundTaskManager
    ):
        mgr = _install_manager
        rec = await mgr.register(
            description="slow task",
            prompt="do work",
            subagent_type="general-purpose",
            model=None,
            parent_session_id=None,
            run_coro_factory=_FakeFactory(sleep=2.0, result=_make_result("never")),
        )
        tool = TaskOutputTool()
        res = await tool.execute(TaskOutputParams(task_id=rec.task_id, block=False))
        assert res.success is True
        assert "still running" in (res.result or "")
        # Doesn't drain — the task is still in flight.
        assert mgr.drain_completed() == []
        await mgr.cancel(rec.task_id)

    async def test_block_true_waits_for_completion(self, _install_manager: BackgroundTaskManager):
        mgr = _install_manager
        rec = await mgr.register(
            description="d",
            prompt="p",
            subagent_type="general-purpose",
            model=None,
            parent_session_id=None,
            run_coro_factory=_FakeFactory(sleep=0.02, result=_make_result("the answer")),
        )
        tool = TaskOutputTool()
        res = await tool.execute(TaskOutputParams(task_id=rec.task_id, timeout=2.0))
        assert res.success is True
        assert res.result == "the answer"
        # The record is marked notified so the inter-turn drain won't
        # re-deliver it.
        assert rec.notified is True

    async def test_block_true_timeout(self, _install_manager: BackgroundTaskManager):
        mgr = _install_manager
        rec = await mgr.register(
            description="d",
            prompt="p",
            subagent_type="general-purpose",
            model=None,
            parent_session_id=None,
            run_coro_factory=_FakeFactory(sleep=2.0, result=_make_result("never")),
        )
        tool = TaskOutputTool()
        res = await tool.execute(TaskOutputParams(task_id=rec.task_id, timeout=0.05))
        assert res.success is True
        assert "did not finish" in (res.result or "")
        # The task is still running — drain should be empty.
        assert mgr.drain_completed() == []
        await mgr.cancel(rec.task_id)

    async def test_completed_task_returns_final_text(
        self, _install_manager: BackgroundTaskManager
    ):
        mgr = _install_manager
        rec = await mgr.register(
            description="d",
            prompt="p",
            subagent_type="general-purpose",
            model=None,
            parent_session_id=None,
            run_coro_factory=_FakeFactory(sleep=0.02, result=_make_result("done")),
        )
        assert rec.asyncio_task is not None
        await rec.asyncio_task
        # Do NOT call drain yet — TaskOutput should still find it.
        tool = TaskOutputTool()
        res = await tool.execute(TaskOutputParams(task_id=rec.task_id))
        assert res.success is True
        assert res.result == "done"
        # TaskOutput acks the notification.
        assert rec.notified is True
        # Drain returns nothing now.
        assert mgr.drain_completed() == []

    async def test_completed_task_truncates_long_text(
        self, _install_manager: BackgroundTaskManager
    ):
        mgr = _install_manager
        long_text = "x" * (33_000)
        rec = await mgr.register(
            description="d",
            prompt="p",
            subagent_type="general-purpose",
            model=None,
            parent_session_id=None,
            run_coro_factory=_FakeFactory(sleep=0.02, result=_make_result(long_text)),
        )
        assert rec.asyncio_task is not None
        await rec.asyncio_task
        tool = TaskOutputTool()
        res = await tool.execute(TaskOutputParams(task_id=rec.task_id))
        assert res.success is True
        assert len(res.result or "") == 32_000
        # No truncation marker leaks into the LLM-facing result.
        assert "truncated" not in (res.result or "").lower()

    async def test_no_manager_returns_error(self, monkeypatch):
        from vtx.tools import task_output

        monkeypatch.setattr(task_output, "get_manager", lambda: None)
        tool = TaskOutputTool()
        res = await tool.execute(TaskOutputParams(task_id="bg_anything"))
        assert res.success is False
        assert "no BackgroundTaskManager" in (res.result or "")


def _make_result(text: str):
    return type(
        "R",
        (),
        {
            "final_text": text,
            "turns": 1,
            "usage": type("U", (), {"total_tokens": 1})(),
            "error": None,
            "stop_reason": type("S", (), {"value": "stop"})(),
        },
    )()

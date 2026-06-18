"""Tests for the background-sub-agent infrastructure.

Covers ``BackgroundTaskManager`` lifecycle, persistence, ack-on-consume
drain semantics, and cancellation contract. The companion TaskOutput
tool has its own tests in ``test_task_output.py``.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from vtx.tools.background import BackgroundTaskManager, get_manager, reset_manager, set_manager

pytestmark = pytest.mark.asyncio


class FakeResult:
    def __init__(self, final_text: str = "ok", turns: int = 1, total_tokens: int = 42):
        self.final_text = final_text
        self.turns = turns
        self.usage = type("U", (), {"total_tokens": total_tokens})()
        self.error: str | None = None
        self.stop_reason = type("S", (), {"value": "stop"})()


class FailingResult:
    def __init__(self, msg: str = "boom"):
        self.final_text = ""
        self.turns = 0
        self.usage = type("U", (), {"total_tokens": 0})()
        self.error = msg
        self.stop_reason = type("S", (), {"value": "error"})()


async def _sleep_then(after: float, value: FakeResult) -> FakeResult:
    await asyncio.sleep(after)
    return value


class TestManagerContextVars:
    @pytest.fixture(autouse=True)
    def _clean_contextvar(self):
        """Ensure the manager slot is ``None`` at test entry.

        Other test files construct a :class:`ConversationRuntime` whose
        :meth:`ensure_background_manager` calls :func:`set_manager`
        without an explicit reset. A leak from a sibling test would
        silently break these isolated assertions.
        """
        from vtx.tools.background import reset_manager

        reset_manager()
        yield
        reset_manager()

    def test_set_and_get_manager(self):
        token = set_manager(None)
        assert get_manager() is None
        mgr = BackgroundTaskManager(store_dir=Path("/tmp/vtx-bg-test-doesnotexist"))
        tok = set_manager(mgr)
        try:
            assert get_manager() is mgr
        finally:
            reset_manager(tok)
            reset_manager(token)
            assert get_manager() is None

    def test_reset_without_set_is_safe(self):
        # ContextVars.reset on a fresh token should be a no-op.
        token = set_manager(None)
        reset_manager(token)
        assert get_manager() is None

    def test_reset_from_different_context_is_safe(self):
        """The runtime is initialised in one Context and torn down in another.

        ``ContextVar.reset(token)`` raises ``ValueError`` when called
        from a different Context than the one that produced ``token``
        — the exact failure mode that produced the noisy
        ``Failed to reset background manager contextvar`` traceback on
        shutdown. ``reset_manager`` must degrade to clearing the slot
        in the current Context instead.
        """
        import contextvars

        mgr = BackgroundTaskManager(store_dir=Path("/tmp/vtx-bg-test-doesnotexist"))
        outer_ctx = contextvars.copy_context()
        token = outer_ctx.run(set_manager, mgr)
        # ``set_manager`` ran in ``outer_ctx``; ``token`` belongs to it.
        # Now run ``reset_manager`` from a *different* Context.
        inner_ctx = contextvars.Context()
        inner_ctx.run(reset_manager, token)
        # The slot is cleared in the current (inner) Context.
        assert get_manager() is None
        # And the original Context still observes the original value
        # — we cannot reach across contexts, and that is by design.
        assert outer_ctx.run(get_manager) is mgr
        # Final cleanup of the outer Context.
        outer_ctx.run(reset_manager, token)
        assert outer_ctx.run(get_manager) is None

    def test_reset_with_no_token_clears_slot(self):
        mgr = BackgroundTaskManager(store_dir=Path("/tmp/vtx-bg-test-doesnotexist"))
        set_manager(mgr)
        assert get_manager() is mgr
        reset_manager()
        assert get_manager() is None


class TestRegistration:
    async def test_register_returns_running_record(self, tmp_path: Path):
        mgr = BackgroundTaskManager(store_dir=tmp_path)
        record = await mgr.register(
            description="Find auth bug",
            prompt="Look at login.py",
            subagent_type="Explore",
            model=None,
            parent_session_id=None,
            run_coro_factory=lambda: _sleep_then(0.05, FakeResult("done")),
        )
        assert record.status == "running"
        assert record.task_id.startswith("bg_")
        assert record.description == "Find auth bug"
        assert record.asyncio_task is not None
        # Initial record was persisted to disk before the task ran.
        on_disk = json.loads((tmp_path / f"{record.task_id}.json").read_text())
        assert on_disk["status"] == "running"
        assert on_disk["description"] == "Find auth bug"
        # Cleanup so we don't leak the background task.
        await mgr.close()

    async def test_register_assigns_distinct_ids(self, tmp_path: Path):
        mgr = BackgroundTaskManager(store_dir=tmp_path)
        ids: set[str] = set()
        for _ in range(3):
            rec = await mgr.register(
                description="d",
                prompt="p",
                subagent_type="general-purpose",
                model=None,
                parent_session_id=None,
                run_coro_factory=lambda: _sleep_then(0.2, FakeResult("x")),
            )
            ids.add(rec.task_id)
        assert len(ids) == 3
        await mgr.close()

    async def test_completed_record_carries_result(self, tmp_path: Path):
        mgr = BackgroundTaskManager(store_dir=tmp_path)
        record = await mgr.register(
            description="d",
            prompt="p",
            subagent_type="general-purpose",
            model=None,
            parent_session_id=None,
            run_coro_factory=lambda: _sleep_then(0.05, FakeResult("the answer", 3, 999)),
        )
        assert record.asyncio_task is not None
        await record.asyncio_task
        assert record.status == "completed"
        assert record.result_text == "the answer"
        assert record.turns == 3
        assert record.total_tokens == 999
        # Disk reflects completion.
        on_disk = json.loads((tmp_path / f"{record.task_id}.json").read_text())
        assert on_disk["status"] == "completed"
        assert on_disk["result_text"] == "the answer"
        await mgr.close()

    async def test_factory_exception_marks_error(self, tmp_path: Path):
        async def boom() -> FakeResult:
            raise RuntimeError("kaboom")

        mgr = BackgroundTaskManager(store_dir=tmp_path)
        record = await mgr.register(
            description="d",
            prompt="p",
            subagent_type="general-purpose",
            model=None,
            parent_session_id=None,
            run_coro_factory=boom,
        )
        assert record.asyncio_task is not None
        await record.asyncio_task
        assert record.status == "error"
        assert record.error is not None and "kaboom" in record.error
        await mgr.close()


class TestDrainSemantics:
    """Ack-on-consume: drain_completed returns each task at most once."""

    async def test_drain_returns_running_then_completed(self, tmp_path: Path):
        mgr = BackgroundTaskManager(store_dir=tmp_path)
        record = await mgr.register(
            description="d",
            prompt="p",
            subagent_type="general-purpose",
            model=None,
            parent_session_id=None,
            run_coro_factory=lambda: _sleep_then(0.05, FakeResult("done")),
        )
        # First drain while still running: should be empty.
        assert mgr.drain_completed() == []
        assert record.asyncio_task is not None
        await record.asyncio_task
        drained = mgr.drain_completed()
        assert len(drained) == 1
        assert drained[0].task_id == record.task_id
        # Second drain returns nothing — the record is already notified.
        assert mgr.drain_completed() == []
        await mgr.close()

    async def test_drain_burst_no_duplicates(self, tmp_path: Path):
        mgr = BackgroundTaskManager(store_dir=tmp_path)
        records = []
        for i in range(5):
            rec = await mgr.register(
                description=f"d{i}",
                prompt="p",
                subagent_type="general-purpose",
                model=None,
                parent_session_id=None,
                run_coro_factory=lambda i=i: _sleep_then(0.01, FakeResult(f"r{i}")),
            )
            records.append(rec)
        await asyncio.gather(*(r.asyncio_task for r in records if r.asyncio_task is not None))
        drained = mgr.drain_completed()
        assert {r.task_id for r in drained} == {r.task_id for r in records}
        # Second drain is empty even though 5 tasks all completed at once.
        assert mgr.drain_completed() == []
        await mgr.close()

    async def test_drain_skips_cancelled(self, tmp_path: Path):
        mgr = BackgroundTaskManager(store_dir=tmp_path)

        async def slow() -> FakeResult:
            await asyncio.sleep(5)
            return FakeResult("never")

        record = await mgr.register(
            description="d",
            prompt="p",
            subagent_type="general-purpose",
            model=None,
            parent_session_id=None,
            run_coro_factory=slow,
        )
        cancelled = await mgr.cancel(record.task_id)
        assert cancelled is True
        drained = mgr.drain_completed()
        assert len(drained) == 1
        assert drained[0].status == "cancelled"
        await mgr.close()

    async def test_drain_skips_error(self, tmp_path: Path):
        mgr = BackgroundTaskManager(store_dir=tmp_path)

        async def fail() -> FakeResult:
            raise RuntimeError("nope")

        record = await mgr.register(
            description="d",
            prompt="p",
            subagent_type="general-purpose",
            model=None,
            parent_session_id=None,
            run_coro_factory=fail,
        )
        assert record.asyncio_task is not None
        await record.asyncio_task
        drained = mgr.drain_completed()
        assert len(drained) == 1
        assert drained[0].status == "error"
        await mgr.close()


class TestWaitAndCancel:
    async def test_wait_returns_after_completion(self, tmp_path: Path):
        mgr = BackgroundTaskManager(store_dir=tmp_path)
        record = await mgr.register(
            description="d",
            prompt="p",
            subagent_type="general-purpose",
            model=None,
            parent_session_id=None,
            run_coro_factory=lambda: _sleep_then(0.05, FakeResult("x")),
        )
        finished = await mgr.wait(record.task_id, timeout=2.0, cancel_event=None)
        assert finished.status == "completed"
        assert finished.result_text == "x"
        await mgr.close()

    async def test_wait_unknown_id_raises_key_error(self, tmp_path: Path):
        mgr = BackgroundTaskManager(store_dir=tmp_path)
        with pytest.raises(KeyError):
            await mgr.wait("bg_9999_doesnotexist", timeout=1.0, cancel_event=None)
        await mgr.close()

    async def test_wait_timeout_raises(self, tmp_path: Path):
        mgr = BackgroundTaskManager(store_dir=tmp_path)

        async def long_run() -> FakeResult:
            await asyncio.sleep(5)
            return FakeResult()

        record = await mgr.register(
            description="d",
            prompt="p",
            subagent_type="general-purpose",
            model=None,
            parent_session_id=None,
            run_coro_factory=long_run,
        )
        with pytest.raises(TimeoutError):
            await mgr.wait(record.task_id, timeout=0.05, cancel_event=None)
        cancelled = await mgr.cancel(record.task_id)
        assert cancelled is True

    async def test_cancel_after_completion_is_false(self, tmp_path: Path):
        mgr = BackgroundTaskManager(store_dir=tmp_path)
        record = await mgr.register(
            description="d",
            prompt="p",
            subagent_type="general-purpose",
            model=None,
            parent_session_id=None,
            run_coro_factory=lambda: _sleep_then(0.02, FakeResult()),
        )
        assert record.asyncio_task is not None
        await record.asyncio_task
        assert await mgr.cancel(record.task_id) is False
        await mgr.close()


class TestDiskRehydration:
    async def test_rehydrate_reads_persisted_records(self, tmp_path: Path):
        mgr = BackgroundTaskManager(store_dir=tmp_path)
        record = await mgr.register(
            description="d",
            prompt="p",
            subagent_type="Explore",
            model="opus",
            parent_session_id="sess_1",
            run_coro_factory=lambda: _sleep_then(0.02, FakeResult("the answer")),
        )
        assert record.asyncio_task is not None
        await record.asyncio_task
        # Mark the record notified so the on-disk state survives a
        # process restart.
        drained = mgr.drain_completed()
        assert len(drained) == 1
        # Simulate a process restart by constructing a new manager
        # against the same directory.
        mgr2 = BackgroundTaskManager(store_dir=tmp_path)
        rehydrated = mgr2.get(record.task_id)
        assert rehydrated is not None
        assert rehydrated.status == "completed"
        assert rehydrated.result_text == "the answer"
        assert rehydrated.subagent_type == "Explore"
        assert rehydrated.model == "opus"
        assert rehydrated.parent_session_id == "sess_1"
        # already-notified flag survives the rehydrate — the parent
        # must not be told twice.
        assert rehydrated.notified is True
        # And ``drain_completed`` on the rehydrated manager is empty.
        assert mgr2.drain_completed() == []
        await mgr.close()
        await mgr2.close()

    async def test_rehydrate_finds_running_record_as_running(self, tmp_path: Path):
        """A record that was 'running' on disk stays 'running' after rehydrate."""
        mgr = BackgroundTaskManager(store_dir=tmp_path)

        async def slow() -> FakeResult:
            await asyncio.sleep(5)
            return FakeResult()

        record = await mgr.register(
            description="d",
            prompt="p",
            subagent_type="general-purpose",
            model=None,
            parent_session_id=None,
            run_coro_factory=slow,
        )
        mgr2 = BackgroundTaskManager(store_dir=tmp_path)
        rehydrated = mgr2.get(record.task_id)
        assert rehydrated is not None
        assert rehydrated.status == "running"
        await mgr.cancel(record.task_id)


class TestCloseSemantics:
    async def test_close_cancels_running(self, tmp_path: Path):
        mgr = BackgroundTaskManager(store_dir=tmp_path)

        async def slow() -> FakeResult:
            await asyncio.sleep(60)
            return FakeResult()

        record = await mgr.register(
            description="d",
            prompt="p",
            subagent_type="general-purpose",
            model=None,
            parent_session_id=None,
            run_coro_factory=slow,
        )
        await mgr.close()
        assert record.status == "cancelled"
        # Disk shows cancelled state.
        on_disk = json.loads((tmp_path / f"{record.task_id}.json").read_text())
        assert on_disk["status"] == "cancelled"

"""Background sub-agent task manager.

When the :class:`~vtx.tools.task.TaskTool` is invoked with
``background: true``, the sub-agent runs concurrently with the parent
agent instead of blocking the parent turn. This module owns the
long-lived state for those runs.

Two responsibilities:

1. Hold a strong reference to each in-flight :class:`asyncio.Task` so
   it is not garbage-collected mid-run.
2. Persist a JSON record per task to ``~/.vtx/tasks/<task_id>.json``
   so completions survive a crash and can be re-listed later.

Design choices and the Anthropic bugs they mitigate:

- ``drain_completed`` returns and clears — the parent agent loop is
  the only consumer; a task is injected as a notification at most
  once. (Mitigates ``anthropics/claude-code#20679`` — notifications
  piling up in a stuck "Channelling" state.)
- Records are persisted to disk before yielding. (Mitigates
  ``#45581`` / ``#60001`` — notifications dropped when several
  background agents complete near-simultaneously.)
- Completion messages carry an explicit marker tag so the model does
  not treat them as a user turn. (Mitigates ``#35610``.)
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import json
import logging
import os
import tempfile
import uuid
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from ..async_utils import OperationCancelledError
from ..config import get_config_dir

log = logging.getLogger("vtx.tools.background")

TaskStatus = Literal["running", "completed", "error", "cancelled"]

MAX_PERSISTED_RESULT_CHARS = 200_000

# Tag used to mark synthetic completion messages injected between
# turns. The model is told in its system prompt that any message
# containing this tag is a system event, not a user instruction.
BACKGROUND_NOTIFICATION_TAG = "vtx:background-task-completion"

_BACKGROUND_MANAGER_VAR: contextvars.ContextVar[BackgroundTaskManager | None] = (
    contextvars.ContextVar("vtx_background_manager", default=None)
)


def set_manager(mgr: BackgroundTaskManager | None) -> contextvars.Token:
    """Install ``mgr`` as the process-local manager.

    Returns the :class:`contextvars.Token` so callers can restore the
    previous value via :meth:`contextvars.ContextVar.reset`. The
    :class:`vtx.runtime.ConversationRuntime` calls this on init and
    on :meth:`ConversationRuntime.close`.
    """
    return _BACKGROUND_MANAGER_VAR.set(mgr)


def get_manager() -> BackgroundTaskManager | None:
    """Return the manager installed by :func:`set_manager`, or ``None``."""
    return _BACKGROUND_MANAGER_VAR.get()


def reset_manager(token: contextvars.Token | None = None) -> None:
    """Restore the manager slot to whatever it was before ``token``.

    ``ContextVar.reset`` only succeeds when called from the same
    :class:`contextvars.Context` that produced ``token``. The runtime
    is initialised in one context (e.g. the main task) but torn down
    in another (e.g. the TUI's ``on_unmount`` or the headless
    ``finally``), so a cross-context reset raises ``ValueError``. Fall
    back to clearing the slot via ``set(None)`` in those cases — that
    is the contract callers actually rely on.
    """
    if token is not None:
        try:
            _BACKGROUND_MANAGER_VAR.reset(token)
            return
        except ValueError:
            # Token was created in a different Context; clear the slot
            # in *this* context instead.
            pass
    _BACKGROUND_MANAGER_VAR.set(None)


@dataclass
class BackgroundTaskRecord:
    """Public, serialisable view of a background task."""

    task_id: str
    description: str
    prompt: str
    subagent_type: str
    model: str | None
    parent_session_id: str | None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    status: TaskStatus = "running"
    result_text: str | None = None
    turns: int = 0
    total_tokens: int = 0
    error: str | None = None
    notified: bool = False

    # Non-serialised runtime handles. Kept off the on-disk shape so the
    # JSON file is safe to read with any JSON parser.
    asyncio_task: asyncio.Task | None = field(default=None, repr=False, compare=False)
    result_future: asyncio.Future | None = field(default=None, repr=False, compare=False)

    def short_id(self) -> str:
        """Return an 8-char prefix suitable for UI display."""
        return self.task_id[:8]


class BackgroundTaskManager:
    """Owns the lifecycle of every background sub-agent.

    Created by :class:`vtx.runtime.ConversationRuntime` and installed
    via :func:`set_manager`. The :class:`TaskTool` reads it from the
    dispatcher context; the parent agent loop reads it for
    completion notifications.
    """

    def __init__(self, *, store_dir: Path | None = None) -> None:
        self._store_dir: Path = store_dir or (get_config_dir() / "tasks")
        self._store_dir.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            self._store_dir.chmod(0o700)
        self._records: dict[str, BackgroundTaskRecord] = {}
        self._seq = 0
        self._lock = asyncio.Lock()
        self._rehydrated = False
        self._closed = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Cancel any still-running tasks; flush their final state.

        Called by :meth:`vtx.runtime.ConversationRuntime.close` from
        both the TUI's ``on_unmount`` and the headless ``finally``.
        Bounded so a stuck sub-agent cannot block process exit.
        """
        self._closed = True
        for record in list(self._records.values()):
            if record.status != "running" or record.asyncio_task is None:
                continue
            record.asyncio_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(record.asyncio_task, timeout=2.0)
            if record.status == "running":
                record.status = "cancelled"
                record.completed_at = _now()
            if record.error is None:
                record.error = "Cancelled: parent runtime closed."
            await self._persist_record(record)

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    async def register(
        self,
        *,
        description: str,
        prompt: str,
        subagent_type: str,
        model: str | None,
        parent_session_id: str | None,
        run_coro_factory: Callable[[], Awaitable[Any]],
    ) -> BackgroundTaskRecord:
        """Schedule ``run_coro_factory`` and return a new record.

        ``run_coro_factory`` must return an awaitable yielding a
        result exposing ``final_text``, ``turns``, ``usage`` (with
        ``total_tokens``), ``error``, and ``stop_reason``. The manager
        wraps it so completion, errors, and cancellation all flow
        through ``record`` updates and disk writes.
        """
        async with self._lock:
            self._seq += 1
            task_id = f"bg_{self._seq:04d}_{uuid.uuid4().hex[:6]}"
            record = BackgroundTaskRecord(
                task_id=task_id,
                description=description,
                prompt=prompt,
                subagent_type=subagent_type,
                model=model,
                parent_session_id=parent_session_id,
                created_at=_now(),
                status="running",
                result_future=asyncio.get_event_loop().create_future(),
            )
            self._records[task_id] = record
            await self._persist_record(record)

        wrapper = self._wrap(record, run_coro_factory)
        record.asyncio_task = asyncio.create_task(wrapper(), name=f"vtx-bg-{task_id}")
        return record

    def _wrap(
        self, record: BackgroundTaskRecord, factory: Callable[[], Awaitable[Any]]
    ) -> Callable[[], Coroutine[Any, Any, None]]:
        async def runner() -> None:
            record.started_at = _now()
            try:
                result = await factory()
                record.result_text = getattr(result, "final_text", None) or ""
                record.turns = int(getattr(result, "turns", 0) or 0)
                usage = getattr(result, "usage", None)
                if usage is not None:
                    record.total_tokens = int(getattr(usage, "total_tokens", 0) or 0)
                err = getattr(result, "error", None)
                if err:
                    record.error = str(err)
                    record.status = "error"
                else:
                    record.status = "completed"
            except asyncio.CancelledError:
                record.status = "cancelled"
                if record.error is None:
                    record.error = "Cancelled."
                raise
            except Exception as exc:
                log.exception("Background task %s raised", record.task_id)
                record.status = "error"
                record.error = f"{type(exc).__name__}: {exc}"
            finally:
                record.completed_at = _now()
                await self._persist_record(record)
                if record.result_future is not None and not record.result_future.done():
                    record.result_future.set_result(record)

        return runner

    def get(self, task_id: str) -> BackgroundTaskRecord | None:
        self._ensure_rehydrated()
        return self._records.get(task_id)

    def list_tasks(self) -> list[BackgroundTaskRecord]:
        self._ensure_rehydrated()
        return sorted(self._records.values(), key=lambda r: r.created_at, reverse=True)

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------

    def _ensure_rehydrated(self) -> None:
        if self._rehydrated:
            return
        self._rehydrated = True
        if not self._store_dir.exists():
            return
        for path in self._store_dir.glob("bg_*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            task_id = data.get("task_id") or path.stem
            if task_id in self._records:
                continue
            try:
                record = BackgroundTaskRecord(
                    task_id=task_id,
                    description=data.get("description", ""),
                    prompt=data.get("prompt", ""),
                    subagent_type=data.get("subagent_type", "general-purpose"),
                    model=data.get("model"),
                    parent_session_id=data.get("parent_session_id"),
                    created_at=_parse_iso(data.get("created_at")) or _now(),
                    started_at=_parse_iso(data.get("started_at")),
                    completed_at=_parse_iso(data.get("completed_at")),
                    status=data.get("status", "completed"),
                    result_text=data.get("result_text"),
                    turns=int(data.get("turns", 0) or 0),
                    total_tokens=int(data.get("total_tokens", 0) or 0),
                    error=data.get("error"),
                    notified=bool(data.get("notified", False)),
                    result_future=asyncio.get_event_loop().create_future(),
                )
            except Exception:
                log.exception("Could not rehydrate background task record %s", path)
                continue
            if record.status != "running" and record.result_future is not None:
                record.result_future.set_result(record)
            self._records[task_id] = record

    # ------------------------------------------------------------------
    # Waiting / cancellation
    # ------------------------------------------------------------------

    async def wait(
        self, task_id: str, *, timeout: float, cancel_event: asyncio.Event | None
    ) -> BackgroundTaskRecord:
        """Block until the task finishes, times out, or is cancelled.

        Returns the (already-completed) record on success. Raises
        :class:`asyncio.TimeoutError` on timeout, or propagates
        :class:`vtx.async_utils.OperationCancelledError` when
        ``cancel_event`` fires first.
        """
        record = self.get(task_id)
        if record is None:
            raise KeyError(task_id)
        if record.status != "running":
            return record
        future = record.result_future
        if future is None:
            return record
        if cancel_event is None:
            return await asyncio.wait_for(future, timeout=timeout)
        cancel_wait = asyncio.create_task(cancel_event.wait())
        done_wait = asyncio.create_task(asyncio.wait_for(future, timeout=timeout))
        try:
            done, _pending = await asyncio.wait(
                {done_wait, cancel_wait}, return_when=asyncio.FIRST_COMPLETED
            )
        except BaseException:
            done_wait.cancel()
            cancel_wait.cancel()
            raise
        if cancel_wait in done:
            done_wait.cancel()
            raise OperationCancelledError("Background task wait cancelled.")
        cancel_wait.cancel()
        return done_wait.result()

    async def cancel(self, task_id: str) -> bool:
        """Cancel a running task. Returns ``True`` if it was running."""
        record = self.get(task_id)
        if record is None or record.status != "running":
            return False
        if record.asyncio_task is not None:
            record.asyncio_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(record.asyncio_task, timeout=2.0)
        if record.status == "running":
            record.status = "cancelled"
            record.completed_at = _now()
            if record.error is None:
                record.error = "Cancelled by user."
            await self._persist_record(record)
            if record.result_future is not None and not record.result_future.done():
                record.result_future.set_result(record)
        return True

    # ------------------------------------------------------------------
    # Notifications (ack-on-consume)
    # ------------------------------------------------------------------

    def drain_completed(self) -> list[BackgroundTaskRecord]:
        """Return every record the parent has not yet been told about.

        Each returned record has ``notified`` flipped to ``True`` so
        it is never delivered twice — even if the parent does nothing
        in response. The disk record is updated synchronously so a
        rehydrated manager sees the same state across process
        restarts.
        """
        self._ensure_rehydrated()
        out: list[BackgroundTaskRecord] = []
        for record in self._records.values():
            if record.status == "running":
                continue
            if record.notified:
                continue
            record.notified = True
            out.append(record)
        if out:
            for record in out:
                try:
                    self._persist_record_sync(record)
                except Exception:
                    log.exception("Failed to persist notified flag for %s", record.task_id)
        return out

    # ------------------------------------------------------------------
    # Disk persistence
    # ------------------------------------------------------------------

    async def _persist_record(self, record: BackgroundTaskRecord) -> None:
        path = self._store_dir / f"{record.task_id}.json"
        payload = {
            "task_id": record.task_id,
            "description": record.description,
            "prompt": record.prompt,
            "subagent_type": record.subagent_type,
            "model": record.model,
            "parent_session_id": record.parent_session_id,
            "created_at": _iso(record.created_at),
            "started_at": _iso(record.started_at) if record.started_at else None,
            "completed_at": _iso(record.completed_at) if record.completed_at else None,
            "status": record.status,
            "result_text": (record.result_text or "")[:MAX_PERSISTED_RESULT_CHARS],
            "turns": record.turns,
            "total_tokens": record.total_tokens,
            "error": record.error,
            "notified": record.notified,
        }
        try:
            _atomic_write_json(path, payload)
        except OSError:
            log.exception("Failed to persist background task record %s", record.task_id)

    def _persist_record_sync(self, record: BackgroundTaskRecord) -> None:
        """Synchronously persist ``record`` to disk.

        Used by :meth:`drain_completed` (which is itself synchronous)
        and by :meth:`close` paths where the caller needs the
        ``notified`` flag to survive a process restart.
        """
        path = self._store_dir / f"{record.task_id}.json"
        payload = {
            "task_id": record.task_id,
            "description": record.description,
            "prompt": record.prompt,
            "subagent_type": record.subagent_type,
            "model": record.model,
            "parent_session_id": record.parent_session_id,
            "created_at": _iso(record.created_at),
            "started_at": _iso(record.started_at) if record.started_at else None,
            "completed_at": _iso(record.completed_at) if record.completed_at else None,
            "status": record.status,
            "result_text": (record.result_text or "")[:MAX_PERSISTED_RESULT_CHARS],
            "turns": record.turns,
            "total_tokens": record.total_tokens,
            "error": record.error,
            "notified": record.notified,
        }
        try:
            _atomic_write_json(path, payload)
        except OSError:
            log.exception("Failed to persist background task record %s", record.task_id)


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.isoformat()


def _parse_iso(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, indent=2, sort_keys=True)
    fd, tmp_path = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(data)
        os.replace(tmp_path, path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise


__all__ = [
    "BACKGROUND_NOTIFICATION_TAG",
    "BackgroundTaskManager",
    "BackgroundTaskRecord",
    "get_manager",
    "reset_manager",
    "set_manager",
]

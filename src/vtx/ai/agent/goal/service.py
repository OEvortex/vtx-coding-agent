"""GoalService: the sole mutation boundary for goal state.

Pipeline for every mutation (mirrors pi-goal-x's GoalService)::

    reconcile (disk wins over stale memory)
      -> validate
      -> mutate a clone (never the live object)
      -> write or archive the active file
      -> append ledger events (best-effort)
      -> publish the new record

If the write fails, nothing commits and nothing is appended.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from . import storage
from .record import (
    GoalRecord,
    TaskRecord,
    clone_record,
    count_tasks,
    create_record,
    normalize_task_ids,
    progress_percent,
    utc_now_iso,
)

log = logging.getLogger("agent.goal")


class GoalError(Exception):
    """Raised when a mutation is invalid for the current goal state."""


FocusListener = Callable[[str | None, str], None]


class GoalService:
    """Project-scoped goal pool plus session focus and mutations."""

    def __init__(self, cwd: str) -> None:
        self.cwd = cwd
        self.focused_id: str | None = None
        self.on_focus_change: FocusListener | None = None

    # ---- settings ---------------------------------------------------------

    @property
    def settings(self) -> dict:
        return storage.load_settings(self.cwd)

    def update_settings(self, **changes: bool) -> dict:
        settings = self.settings
        for key, value in changes.items():
            if key in settings and isinstance(value, bool):
                settings[key] = value
        storage.save_settings(self.cwd, settings)
        return settings

    # ---- pool / focus -----------------------------------------------------

    def pool(self) -> dict[str, GoalRecord]:
        return storage.read_active_pool(self.cwd)

    def get(self, goal_id: str) -> GoalRecord | None:
        record = self.pool().get(goal_id)
        archived = self._read_any(goal_id) if record is None else None
        return record or archived

    def _read_any(self, goal_id: str) -> GoalRecord | None:
        path = storage.find_goal_file(self.cwd, goal_id)
        if path is None:
            return None
        try:
            return storage.parse(path.read_text(encoding="utf-8"))
        except OSError:
            return None

    def focused(self) -> GoalRecord | None:
        """The focused open goal, re-read from disk (disk wins)."""
        if not self.focused_id:
            return None
        record = self.get(self.focused_id)
        if record is None or not record.is_open():
            return None
        return record

    def set_focus(
        self, goal_id: str | None, *, reason: str = "selected", announce: bool = False
    ) -> None:
        changed = self.focused_id != goal_id
        self.focused_id = goal_id
        storage.append_ledger(self.cwd, "focus_changed", goal_id, reason=reason)
        if changed and self.on_focus_change is not None:
            try:
                self.on_focus_change(goal_id, reason)
            except Exception:
                log.exception("goal focus listener failed")
        del announce

    def unfocus(self, *, reason: str = "unfocused") -> None:
        self.set_focus(None, reason=reason)

    # ---- mutations ---------------------------------------------------------

    def create(
        self,
        objective: str,
        *,
        mode: str = "regular",
        verification: str = "",
        token_budget: int | None = None,
        focus: bool = True,
        source: str = "create_goal",
    ) -> GoalRecord:
        record = create_record(
            objective, mode=mode, verification=verification, token_budget=token_budget
        )
        record.updated_at = utc_now_iso()
        storage.write_active(self.cwd, record)
        storage.append_ledger(self.cwd, "created", record.id, mode=record.mode, source=source)
        if focus:
            self.set_focus(record.id, reason="created")
        return clone_record(record)

    def mutate(
        self,
        goal_id: str,
        mutator: Callable[[GoalRecord], None],
        *,
        ledger_events: list[dict[str, Any]] | None = None,
        expect_status_open: bool = True,
    ) -> GoalRecord:
        """Reconcile → clone-mutate → write → ledger → publish."""
        current = self.get(goal_id)
        if current is None:
            raise GoalError(f"Goal {goal_id!r} not found")
        if expect_status_open and not current.is_open():
            raise GoalError(f"Goal {goal_id!r} is already closed ({current.status})")

        updated = clone_record(current)
        mutator(updated)
        updated.revision = current.revision + 1
        updated.updated_at = utc_now_iso()

        storage.write_active(self.cwd, updated)
        for event in ledger_events or []:
            payload = {"goal_id": updated.id, **event}
            etype = payload.pop("type", "updated")
            storage.append_ledger(self.cwd, etype, **payload)
        return clone_record(updated)

    def set_status(
        self,
        goal_id: str,
        status: str,
        *,
        reason: str | None = None,
        completion_summary: str | None = None,
        review_feedback: str | None = None,
    ) -> GoalRecord:
        events: list[dict] = [{"type": "status_changed", "status": status}]
        if reason:
            events[0]["reason"] = reason[:500]

        def apply(record: GoalRecord) -> None:
            record.status = status
            record.blocked_reason = (
                reason[:500]
                if status == "blocked" and reason
                else (record.blocked_reason if status == "blocked" else None)
            )
            record.paused_reason = reason[:500] if status == "paused" and reason else None
            if status == "complete":
                record.completion_summary = (completion_summary or "")[:2000] or None
                if review_feedback:
                    record.review_feedback = review_feedback[:2000]
            elif status == "active":
                record.review_feedback = (
                    review_feedback[:2000] if review_feedback else (record.review_feedback)
                )

        return self.mutate(goal_id, apply, ledger_events=events)

    def tweak(self, goal_id: str, change: str, *, new_objective: str | None = None) -> GoalRecord:
        change = (change or "").strip()
        if not change:
            raise GoalError("Tweak description must not be empty")

        def apply(record: GoalRecord) -> None:
            if new_objective:
                record.objective = new_objective.strip()

        return self.mutate(
            goal_id, apply, ledger_events=[{"type": "tweaked", "change": change[:500]}]
        )

    def replace_tasks(self, goal_id: str, items: list[dict]) -> GoalRecord:
        tasks = normalize_task_ids(items)

        def apply(record: GoalRecord) -> None:
            old_by_key = {(t.parent_id, t.title.lower()): t for t in record.tasks}
            merged: list[TaskRecord] = []
            for task in tasks:
                previous = old_by_key.get((task.parent_id, task.title.lower()))
                if previous is not None and previous.status != "pending":
                    task.status = previous.status
                    task.evidence = previous.evidence
                    task.note = previous.note
                merged.append(task)
            record.tasks = merged
            if record.current_task_id and not any(t.id == record.current_task_id for t in merged):
                record.current_task_id = None

        done, total = count_tasks(tasks)
        return self.mutate(
            goal_id,
            apply,
            ledger_events=[
                {"type": "task_list_set", "tasks": len(tasks), "done": done, "total": total}
            ],
        )

    def update_task(
        self,
        goal_id: str,
        task_id: str,
        status: str,
        *,
        evidence: str = "",
        note: str = "",
        subtasks: list[dict] | None = None,
    ) -> GoalRecord:
        task_id = (task_id or "").strip()

        def find_target(record: GoalRecord) -> tuple[TaskRecord, int]:
            for index, task in enumerate(record.tasks):
                if task.id == task_id:
                    return task, index
            raise GoalError(f"Task {task_id!r} not found")

        def apply(record: GoalRecord) -> None:
            target, index = find_target(record)
            if status == "start":
                record.current_task_id = target.id
                return
            if status == "pending" and target.status == "skipped":
                target.status = "pending"
                target.note = note[:500]
                return
            target.status = status
            if status == "complete":
                contract = _task_contract(record, target)
                if contract and not evidence.strip():
                    raise GoalError(
                        f"Task {target.id} has a verification contract; "
                        "provide evidence when completing it"
                    )
                target.evidence = evidence.strip()[:1000]
                if record.current_task_id == target.id:
                    record.current_task_id = None
                _autostart_next(record, index)
            elif status == "skipped":
                target.note = (note or evidence).strip()[:500]
                if record.current_task_id == target.id:
                    record.current_task_id = None
                _autostart_next(record, index)
            if subtasks:
                existing = [t for t in record.tasks if t.parent_id != target.id]
                children = normalize_task_ids(
                    [{**item, "parent_id": target.id} for item in subtasks]
                )
                insert_at = (
                    index
                    + 1
                    + sum(1 for t in record.tasks[index + 1 :] if t.parent_id == target.id)
                )
                record.tasks[:] = existing
                record.tasks[insert_at:insert_at] = children

        events = [{"type": "task_updated", "task_id": task_id, "status": status}]
        return self.mutate(goal_id, apply, ledger_events=events)

    def charge_usage(
        self, goal_id: str, *, input_tokens: int, output_tokens: int, elapsed_ms: float
    ) -> GoalRecord | None:
        """Serialized idempotent accounting; never raises."""

        def apply(record: GoalRecord) -> None:
            record.usage.input_tokens += max(0, input_tokens)
            record.usage.output_tokens += max(0, output_tokens)
            record.usage.elapsed_ms += max(0.0, elapsed_ms)
            if (
                record.token_budget is not None
                and record.usage.total_tokens() >= record.token_budget
                and record.status == "active"
            ):
                record.status = "budget_limited"

        try:
            return self.mutate(goal_id, apply)
        except GoalError:
            return None

    def archive(self, goal_id: str, *, status: str = "complete") -> GoalRecord:
        current = self.get(goal_id)
        if current is None:
            raise GoalError(f"Goal {goal_id!r} not found")
        final = clone_record(current)
        final.status = status
        final.updated_at = utc_now_iso()
        storage.archive(self.cwd, final)
        storage.append_ledger(self.cwd, "archived", final.id, status=status)
        if self.focused_id == goal_id:
            self.set_focus(None, reason="cleared")
        return final


def _task_contract(record: GoalRecord, task: TaskRecord) -> str:
    """Per-task verification contract parsed from a ``Contract:`` note prefix."""
    lowered = task.note.strip()
    if lowered.lower().startswith("contract:"):
        return lowered[len("contract:") :].strip()
    return ""


def _autostart_next(record: GoalRecord, just_finished_index: int) -> None:
    """Sisyphus-style pointer advance: next pending sibling or top task."""
    for task in record.tasks[just_finished_index + 1 :]:
        if task.status == "pending" and not task.parent_id:
            record.current_task_id = task.id
            return
    record.current_task_id = None


def goal_progress(record: GoalRecord) -> tuple[int, int, int]:
    done, total = count_tasks(record.tasks)
    return done, total, progress_percent(done, total)


_service_cache: dict[str, GoalService] = {}


def get_service(cwd: str) -> GoalService:
    service = _service_cache.get(cwd)
    if service is None:
        service = _service_cache[cwd] = GoalService(cwd)
    return service

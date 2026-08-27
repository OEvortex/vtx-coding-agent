"""GoalService: the sole mutation boundary for goal state."""

from __future__ import annotations

import logging
from collections.abc import Callable

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
        storage.append_ledger(self.cwd, "settings_updated", **changes)
        return settings

    # ---- queries ----------------------------------------------------------

    def pool(self) -> dict[str, GoalRecord]:
        return storage.read_active_pool(self.cwd)

    def focused(self) -> GoalRecord | None:
        if self.focused_id:
            record = self.pool().get(self.focused_id)
            if record is not None:
                return record
        if self.settings.get("autoSelectSingleGoal", True):
            pool = self.pool()
            if len(pool) == 1:
                single_id = next(iter(pool))
                self.focused_id = single_id
                return pool[single_id]
        return None

    def get(self, goal_id: str) -> GoalRecord | None:
        return self.pool().get(goal_id)

    # ---- focus management -------------------------------------------------

    def set_focus(self, goal_id: str | None) -> GoalRecord | None:
        prev = self.focused_id
        if goal_id is None:
            self.focused_id = None
            if prev != self.focused_id:
                storage.append_ledger(self.cwd, "focus_changed", goal_id=None, previous=prev)
                self._notify_focus_change(None, "unfocused")
            return None
        pool = self.pool()
        if goal_id not in pool:
            raise GoalError(f"Cannot focus goal {goal_id!r}: not an open goal in this project")
        self.focused_id = goal_id
        record = pool[goal_id]
        if prev != self.focused_id:
            storage.append_ledger(self.cwd, "focus_changed", goal_id=goal_id, previous=prev)
            self._notify_focus_change(goal_id, "focused")
        return record

    def clear_focus(self) -> None:
        self.set_focus(None)

    def _notify_focus_change(self, goal_id: str | None, reason: str) -> None:
        if self.on_focus_change:
            try:
                self.on_focus_change(goal_id, reason)
            except Exception as exc:
                log.warning("focus listener failed: %s", exc)

    # ---- mutations --------------------------------------------------------

    def create(
        self,
        objective: str,
        *,
        mode: str = "regular",
        verification: str = "",
        token_budget: int | None = None,
        tasks: list[dict] | None = None,
        auto_focus: bool = True,
    ) -> GoalRecord:
        now = utc_now_iso()
        record = create_record(
            objective, mode=mode, verification=verification, token_budget=token_budget, now=now
        )
        if tasks:
            record.tasks = normalize_task_ids(tasks)
        storage.write_active(self.cwd, record)
        storage.append_ledger(
            self.cwd,
            "created",
            goal_id=record.id,
            mode=record.mode,
            objective=record.objective,
            task_count=len(record.tasks),
        )
        if auto_focus:
            self.set_focus(record.id)
        return record

    def update_status(
        self,
        goal_id: str,
        status: str,
        *,
        reason: str | None = None,
        completion_summary: str | None = None,
    ) -> GoalRecord:
        record = self._require(goal_id)
        clone = clone_record(record)
        now = utc_now_iso()
        clone.updated_at = now
        clone.revision += 1

        if status == "active":
            clone.status = "active"
            clone.blocked_reason = None
            clone.paused_reason = None
        elif status == "paused":
            clone.status = "paused"
            clone.paused_reason = reason or "Paused by user"
        elif status == "blocked":
            if not reason:
                raise GoalError("A reason is required when marking a goal blocked")
            clone.status = "blocked"
            clone.blocked_reason = reason
        elif status == "budget_limited":
            clone.status = "budget_limited"
        elif status == "complete":
            clone.status = "complete"
            clone.completion_summary = completion_summary or reason or "Completed"
        else:
            raise GoalError(f"Unknown status {status!r}")

        storage.write_active(self.cwd, clone)
        storage.append_ledger(
            self.cwd, "status_changed", goal_id=clone.id, status=clone.status, reason=reason
        )
        return clone

    def tweak(
        self,
        goal_id: str,
        *,
        objective: str | None = None,
        verification: str | None = None,
        token_budget: int | None = None,
        change_summary: str = "",
    ) -> GoalRecord:
        record = self._require(goal_id)
        clone = clone_record(record)
        clone.updated_at = utc_now_iso()
        clone.revision += 1
        if objective is not None:
            obj = objective.strip()
            if not obj:
                raise GoalError("Objective cannot be empty")
            clone.objective = obj
        if verification is not None:
            clone.verification = verification.strip()
        if token_budget is not None:
            if token_budget <= 0:
                raise GoalError("token_budget must be positive")
            clone.token_budget = token_budget
        storage.write_active(self.cwd, clone)
        storage.append_ledger(
            self.cwd, "tweaked", goal_id=clone.id, change=change_summary or "Goal tweaked"
        )
        return clone

    def set_tasks(self, goal_id: str, raw_tasks: list[dict]) -> GoalRecord:
        record = self._require(goal_id)
        clone = clone_record(record)
        clone.updated_at = utc_now_iso()
        clone.revision += 1
        clone.tasks = normalize_task_ids(raw_tasks)
        if clone.current_task_id and not any(t.id == clone.current_task_id for t in clone.tasks):
            clone.current_task_id = None
        storage.write_active(self.cwd, clone)
        storage.append_ledger(self.cwd, "task_list_set", goal_id=clone.id, count=len(clone.tasks))
        return clone

    def update_task(
        self,
        goal_id: str,
        task_id: str,
        *,
        status: str | None = None,
        title: str | None = None,
        evidence: str | None = None,
        note: str | None = None,
    ) -> tuple[GoalRecord, TaskRecord]:
        record = self._require(goal_id)
        clone = clone_record(record)
        task = next((t for t in clone.tasks if t.id == task_id), None)
        if task is None:
            raise GoalError(f"Task {task_id!r} does not exist in goal {goal_id}")
        if status is not None:
            if status not in ("pending", "complete", "skipped"):
                raise GoalError(f"Invalid task status {status!r}")
            task.status = status
        if title is not None:
            task.title = title.strip()[:300]
        if evidence is not None:
            task.evidence = evidence.strip()
        if note is not None:
            task.note = note.strip()[:500]

        clone.updated_at = utc_now_iso()
        clone.revision += 1
        storage.write_active(self.cwd, clone)
        storage.append_ledger(
            self.cwd,
            "task_updated",
            goal_id=clone.id,
            task_id=task.id,
            status=task.status,
            title=task.title,
        )
        return clone, task

    def record_usage(
        self,
        goal_id: str,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        elapsed_ms: float = 0.0,
    ) -> GoalRecord:
        record = self._require(goal_id)
        clone = clone_record(record)
        clone.usage.input_tokens += max(0, input_tokens)
        clone.usage.output_tokens += max(0, output_tokens)
        clone.usage.elapsed_ms += max(0.0, elapsed_ms)
        clone.updated_at = utc_now_iso()

        if (
            clone.token_budget
            and clone.usage.total_tokens() >= clone.token_budget
            and clone.status == "active"
        ):
            clone.status = "budget_limited"
            storage.append_ledger(
                self.cwd,
                "budget_limited",
                goal_id=clone.id,
                tokens=clone.usage.total_tokens(),
                budget=clone.token_budget,
            )

        storage.write_active(self.cwd, clone)
        return clone

    def archive(
        self, goal_id: str, *, reason: str = "archived", summary: str | None = None
    ) -> GoalRecord:
        record = self._require(goal_id)
        clone = clone_record(record)
        clone.updated_at = utc_now_iso()
        if summary:
            clone.completion_summary = summary
        storage.archive(self.cwd, clone)
        storage.append_ledger(
            self.cwd, "archived", goal_id=clone.id, reason=reason, status=clone.status
        )
        if self.focused_id == goal_id:
            self.set_focus(None)
        return clone

    def record_audit_verdict(
        self, goal_id: str, *, approved: bool, feedback: str = "", summary: str = ""
    ) -> GoalRecord:
        record = self._require(goal_id)
        clone = clone_record(record)
        clone.updated_at = utc_now_iso()
        clone.revision += 1
        if approved:
            clone.status = "complete"
            clone.review_feedback = None
            if summary:
                clone.completion_summary = summary
            storage.archive(self.cwd, clone)
            storage.append_ledger(
                self.cwd,
                "audit_approved",
                goal_id=clone.id,
                summary=summary or "Approved by auditor",
            )
            if self.focused_id == goal_id:
                self.set_focus(None)
        else:
            clone.status = "active"
            clone.review_feedback = feedback or "Changes required by auditor"
            storage.write_active(self.cwd, clone)
            storage.append_ledger(
                self.cwd, "audit_changes_required", goal_id=clone.id, summary=feedback[:200]
            )
        return clone

    def _require(self, goal_id: str) -> GoalRecord:
        record = self.get(goal_id)
        if record is None:
            raise GoalError(f"Goal {goal_id!r} is not open or does not exist")
        return record


_services: dict[str, GoalService] = {}


def get_service(cwd: str) -> GoalService:
    cwd = str(cwd)
    if cwd not in _services:
        _services[cwd] = GoalService(cwd)
    return _services[cwd]


def goal_progress(record: GoalRecord) -> tuple[int, int, int]:
    done, total = count_tasks(record.tasks)
    return done, total, progress_percent(done, total)


__all__ = ["FocusListener", "GoalError", "GoalService", "get_service", "goal_progress"]

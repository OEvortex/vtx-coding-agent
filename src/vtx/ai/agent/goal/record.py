"""Goal record model: statuses, task tree helpers, and record lifecycle.

A :class:`GoalRecord` is the in-memory form of one goal file. Storage
(:mod:`vtx.ai.agent.goal.storage`) serializes it to markdown with an
embedded JSON metadata block; the service layer mutates clones of records
so the live object only ever changes through a successful disk write.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

GOAL_MODES: tuple[str, ...] = ("regular", "sisyphus")
GOAL_STATUSES: tuple[str, ...] = ("active", "paused", "blocked", "budget_limited", "complete")
TASK_STATUSES: tuple[str, ...] = ("pending", "complete", "skipped")

OBJECTIVE_MAX_CHARS = 4000

TASK_ID_RE = re.compile(r"^[a-z0-9]+(\.[a-z0-9]+)*$")


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def new_goal_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass
class GoalUsage:
    """Serialized, idempotent token/time accounting for a goal."""

    input_tokens: int = 0
    output_tokens: int = 0
    elapsed_ms: float = 0.0

    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def to_dict(self) -> dict:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "elapsed_ms": round(self.elapsed_ms, 1),
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> GoalUsage:
        data = data or {}
        return cls(
            input_tokens=max(0, int(data.get("input_tokens") or 0)),
            output_tokens=max(0, int(data.get("output_tokens") or 0)),
            elapsed_ms=max(0.0, float(data.get("elapsed_ms") or 0.0)),
        )


@dataclass
class TaskRecord:
    """One node of the flat parent-linked task tree."""

    id: str
    title: str
    status: str = "pending"
    parent_id: str | None = None
    evidence: str = ""
    note: str = ""

    def to_dict(self) -> dict:
        out: dict = {"id": self.id, "title": self.title, "status": self.status}
        if self.parent_id:
            out["parent_id"] = self.parent_id
        if self.evidence:
            out["evidence"] = self.evidence
        if self.note:
            out["note"] = self.note
        return out

    @classmethod
    def from_dict(cls, data: dict) -> TaskRecord:
        raw_status = data.get("status")
        status = (
            raw_status
            if isinstance(raw_status, str) and raw_status in TASK_STATUSES
            else "pending"
        )
        return cls(
            id=str(data.get("id") or "").strip(),
            title=str(data.get("title") or "").strip(),
            status=status,
            parent_id=(str(data["parent_id"]).strip() or None) if data.get("parent_id") else None,
            evidence=str(data.get("evidence") or ""),
            note=str(data.get("note") or ""),
        )


def clone_record(record: GoalRecord) -> GoalRecord:
    """Deep-copy ``record``; mutation pipelines edit the clone only."""
    return replace(record, tasks=[replace(t) for t in record.tasks], usage=replace(record.usage))


def objective_title(objective: str, max_chars: int = 60) -> str:
    """One-line objective summary for list rows and widget headers."""
    text = re.sub(r"\s+", " ", objective or "").strip()
    if len(text) > max_chars:
        return text[: max_chars - 1].rstrip() + "…"
    return text


def create_record(
    objective: str,
    *,
    mode: str = "regular",
    verification: str = "",
    token_budget: int | None = None,
    now: str | None = None,
) -> GoalRecord:
    objective = (objective or "").strip()
    if not objective:
        raise ValueError("Goal objective must not be empty")
    if len(objective) > OBJECTIVE_MAX_CHARS:
        raise ValueError(
            f"Goal objective exceeds {OBJECTIVE_MAX_CHARS} characters ({len(objective)} given)"
        )
    if mode not in GOAL_MODES:
        raise ValueError(f"Unknown goal mode {mode!r}")
    if token_budget is not None and token_budget <= 0:
        raise ValueError("token_budget must be a positive integer")
    now = now or utc_now_iso()
    return GoalRecord(
        id=new_goal_id(),
        mode=mode,
        status="active",
        objective=objective,
        verification=verification.strip(),
        created_at=now,
        updated_at=now,
        token_budget=token_budget,
    )


def count_tasks(tasks: list[TaskRecord]) -> tuple[int, int]:
    """Return ``(done, total)`` over top-level tasks."""
    top = [t for t in tasks if not t.parent_id]
    done = sum(1 for t in top if t.status == "complete")
    return done, len(top)


def progress_percent(done: int, total: int) -> int:
    if total <= 0:
        return 0
    return round(done * 100 / total)


def current_task(record: GoalRecord) -> TaskRecord | None:
    """The execution focus: explicit pointer first, then first pending task."""
    if record.current_task_id:
        for task in record.tasks:
            if task.id == record.current_task_id:
                return task
    for task in record.tasks:
        if not task.parent_id and task.status == "pending":
            return task
    return None


def normalize_task_ids(items: list[dict]) -> list[TaskRecord]:
    """Build a flat parent-linked task tree from loose dicts."""
    tasks: list[TaskRecord] = []
    seen: set[str] = set()
    counters: dict[str, int] = {}

    for raw in items:
        title = str(raw.get("title") or "").strip()
        if not title:
            continue
        parent_id = str(raw.get("parent_id") or "").strip() or None
        task_id = str(raw.get("id") or "").strip()
        if parent_id and parent_id not in {t.id for t in tasks}:
            parent_id = None
        if not task_id or task_id in seen or not TASK_ID_RE.match(task_id):
            base = parent_id or "t"
            counters[base] = counters.get(base, 0) + 1
            n = counters[base]
            task_id = f"{base}.{n}" if parent_id else f"{base}{n}"
        seen.add(task_id)
        tasks.append(
            TaskRecord(
                id=task_id,
                title=title[:300],
                status="pending",
                parent_id=parent_id,
                note=str(raw.get("note") or "")[:500],
            )
        )
    return tasks


STATUS_LABELS: dict[str, str] = {
    "active": "running",
    "paused": "paused",
    "blocked": "blocked",
    "budget_limited": "budget reached",
    "complete": "complete",
}


@dataclass
class GoalRecord:
    id: str
    mode: str
    status: str
    objective: str
    verification: str = ""
    prompt: str = ""
    tasks: list[TaskRecord] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    revision: int = 1
    current_task_id: str | None = None
    blocked_reason: str | None = None
    paused_reason: str | None = None
    completion_summary: str | None = None
    review_feedback: str | None = None
    token_budget: int | None = None
    usage: GoalUsage = field(default_factory=GoalUsage)

    def label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status)

    def is_open(self) -> bool:
        return self.status in ("active", "paused", "blocked", "budget_limited")

    def to_meta(self) -> dict:
        """Authoritative machine-readable payload embedded in the file."""
        return {
            "version": 1,
            "id": self.id,
            "mode": self.mode,
            "status": self.status,
            "verification": self.verification,
            "tasks": [t.to_dict() for t in self.tasks],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "revision": self.revision,
            "current_task_id": self.current_task_id,
            "blocked_reason": self.blocked_reason,
            "paused_reason": self.paused_reason,
            "completion_summary": self.completion_summary,
            "review_feedback": self.review_feedback,
            "token_budget": self.token_budget,
            "usage": self.usage.to_dict(),
        }

    @classmethod
    def from_meta(cls, meta: dict, prompt_body: str = "", objective_body: str = "") -> GoalRecord:
        raw_mode = meta.get("mode")
        raw_status = meta.get("status")
        record = cls(
            id=str(meta.get("id") or "").strip(),
            mode=raw_mode if isinstance(raw_mode, str) and raw_mode in GOAL_MODES else "regular",
            status=(
                raw_status
                if isinstance(raw_status, str) and raw_status in GOAL_STATUSES
                else "paused"
            ),
            objective=str(meta.get("objective") or objective_body or "").strip(),
            verification=str(meta.get("verification") or ""),
            prompt=prompt_body,
            tasks=[TaskRecord.from_dict(t) for t in meta.get("tasks") or []],
            created_at=str(meta.get("created_at") or ""),
            updated_at=str(meta.get("updated_at") or ""),
            revision=int(meta.get("revision") or 1),
            current_task_id=meta.get("current_task_id") or None,
            blocked_reason=meta.get("blocked_reason") or None,
            paused_reason=meta.get("paused_reason") or None,
            completion_summary=meta.get("completion_summary") or None,
            review_feedback=meta.get("review_feedback") or None,
            token_budget=int(meta["token_budget"]) if meta.get("token_budget") else None,
            usage=GoalUsage.from_dict(meta.get("usage")),
        )
        if not record.objective and objective_body:
            record.objective = objective_body
        return record


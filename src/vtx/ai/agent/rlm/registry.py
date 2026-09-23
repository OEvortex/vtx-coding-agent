"""Parent-side child registry and cross-boundary stores for the RLM host bridge.

One :class:`ChildRegistry` exists per parent session id (the identity the
ipython kernel is bound to). It owns:

- the direct ``rlm.spawn`` children (spawn -> list -> collect -> delete),
- the pending host-notice queue drained by the parent at turn boundaries,
- the pending refine/compact requests scheduled by the kernel mid-turn,
- the per-registry progress-note throttle.

Wiring points for the parent runtime (host.py cannot edit those files):

- :func:`drain_notices`           -> inject as user-channel messages inside
  ``vtx.ai.agent.loop.Agent._drain_background_notifications``
  (``loop.py:311``; call sites ``loop.py:249`` and ``loop.py:294``).
- :func:`drain_pending_refine`    -> run harness refinement after the turn.
- :func:`drain_pending_compact`   -> compact context after the turn
  (``loop.py:_check_compaction``).
"""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

PROGRESS_NOTE_MIN_INTERVAL_MS = 10_000
ANSWER_PREVIEW_MAX_CHARS = 200
LABEL_MAX_CHARS = 200
CHILD_DEPTH = 1
DELETED_COLLECT_ERROR = "Deleted by parent orchestrator"

COLLECT_STATUSES = frozenset({"queued", "running", "done", "error", "cancelled"})
LIST_STATUSES = frozenset({"running", "completed", "error"})


class ChildNameUnavailable(Exception):
    """Spawn name already taken under this parent; message is wire-exact."""


@dataclass
class ChildRecord:
    """One direct child of the spawning parent session."""

    rlm_child_id: str
    name: str
    session_dir: str
    model: str
    prompt: str = ""
    status: str = "running"  # queued | running | done | error | cancelled
    task_id: str | None = None
    answer_preview: str | None = None
    error: str | None = None
    duration_ms: int | None = None
    tool_use_count: int | None = None
    progress_note: str | None = None
    started_at: float | None = None
    last_activity_at: float | None = None
    replied_since_task: bool = False
    deleted: bool = False

    @property
    def settled(self) -> bool:
        return self.status in ("done", "error", "cancelled")

    @property
    def list_status(self) -> str:
        if self.status == "done":
            return "completed"
        if self.status == "error":
            return "error"
        return "running"

    def matches(self, target: str) -> bool:
        return target == self.rlm_child_id or target == self.name

    def list_row(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "rlm_child_id": self.rlm_child_id,
            "session_name": self.name,
            "session_dir": self.session_dir,
            "status": self.list_status,
            "label": self.name[:LABEL_MAX_CHARS],
            "replied_since_task": self.replied_since_task,
            "active_session_id": self.rlm_child_id if self.status == "running" else None,
            "session_id": None,
        }
        if self.tool_use_count is not None:
            row["tool_use_count"] = self.tool_use_count
        if self.duration_ms is not None:
            row["duration_ms"] = self.duration_ms
        if self.answer_preview is not None:
            row["answer_preview"] = self.answer_preview[:ANSWER_PREVIEW_MAX_CHARS]
        if self.progress_note is not None:
            row["progress_note"] = self.progress_note
        if self.last_activity_at is not None:
            row["last_activity_at"] = int(self.last_activity_at)
        return row

    def collect_entry(self) -> dict[str, Any]:
        return {
            "rlm_child_id": self.rlm_child_id,
            "session_name": self.name,
            "session_dir": self.session_dir,
            "status": self.status,
            "settled": self.settled,
            "answer_preview": self.answer_preview,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "tool_use_count": self.tool_use_count,
            "replied_since_task": self.replied_since_task,
        }

    def deleted_collect_entry(self) -> dict[str, Any]:
        entry = self.collect_entry()
        entry["status"] = "cancelled"
        entry["settled"] = True
        if entry["error"] is None:
            entry["error"] = DELETED_COLLECT_ERROR
        return entry


@dataclass
class ChildRegistry:
    """All parent-side state behind one kernel's host bridge."""

    session_id: str | None = None
    children: dict[str, ChildRecord] = field(default_factory=dict)
    notices: list[dict[str, Any]] = field(default_factory=list)
    last_note_at: float | None = None
    progress_note: str | None = None
    refine_pending: dict[str, Any] | None = None
    refine_in_flight: bool = False
    compact_pending: dict[str, Any] | None = None

    # -- children ----------------------------------------------------------
    def spawn(self, *, name: str | None, session_dir: str, model: str, prompt: str) -> ChildRecord:
        child_id = f"child_{uuid.uuid4().hex[:12]}"
        requested = (name or "").strip()
        if requested:
            for existing in self.children.values():
                if existing.deleted or existing.status == "cancelled":
                    continue
                if existing.name == requested:
                    raise ChildNameUnavailable(
                        f'Agent name "{requested}" is unavailable: an agent of that '
                        f"name already exists at depth {CHILD_DEPTH} under this parent"
                    )
        resolved = requested or f"child-{child_id[6:]}"
        record = ChildRecord(
            rlm_child_id=child_id,
            name=resolved,
            session_dir=session_dir,
            model=model,
            prompt=prompt,
            started_at=time.time(),
        )
        self.children[child_id] = record
        return record

    def active(self) -> list[ChildRecord]:
        return [child for child in self.children.values() if not child.deleted]

    def find(self, target: str) -> list[ChildRecord]:
        return [child for child in self.active() if child.matches(target)]

    def find_deleted(self, target: str) -> list[ChildRecord]:
        return [
            child for child in self.children.values() if child.deleted and child.matches(target)
        ]

    def list_rows(self) -> list[dict[str, Any]]:
        return [child.list_row() for child in self.active() if child.status != "cancelled"]

    # -- progress notes ----------------------------------------------------
    def note_progress(self, message: str) -> tuple[bool, int | None]:
        now = time.monotonic() * 1000
        last = self.last_note_at
        if last is not None and now - last < PROGRESS_NOTE_MIN_INTERVAL_MS:
            remaining = int(PROGRESS_NOTE_MIN_INTERVAL_MS - (now - last))
            return (False, max(1, remaining))
        self.last_note_at = now
        self.progress_note = message
        return (True, None)

    # -- notices -----------------------------------------------------------
    def add_notice(self, kind: str, key: Any, text: str) -> None:
        self.notices.append({"kind": kind, "key": key, "text": text})

    def withdraw_notice(self, kind: str, key: Any) -> None:
        self.notices = [
            notice
            for notice in self.notices
            if not (notice["kind"] == kind and notice["key"] == key)
        ]

    def drain_notices(self) -> list[str]:
        pending = self.notices
        self.notices = []
        return [notice["text"] for notice in pending]


_registries: dict[str, ChildRegistry] = {}

_DEFAULT_KEY = "__default__"


def _key(session_id: str | None) -> str:
    if isinstance(session_id, str) and session_id:
        return session_id
    return _DEFAULT_KEY


def bridge_session_id(explicit: str | None = None) -> str:
    """Session key shared by the ipython tool, host dispatcher and parent drains.

    ``tools/ipython.py`` resolves ``params.session_id or VTX_SESSION_ID or
    "default"`` before binding the kernel, so every host request (and its
    queued notices / pending refine+compact) lands under that key. The parent
    loop must compute the identical expression or the drains never find the
    queue. ``_key(None)`` maps to ``__default__``, which is a different bucket.
    """
    if isinstance(explicit, str) and explicit:
        return explicit
    return os.environ.get("VTX_SESSION_ID") or "default"


def get_registry(session_id: str | None) -> ChildRegistry:
    key = _key(session_id)
    registry = _registries.get(key)
    if registry is None:
        registry = ChildRegistry(session_id=session_id)
        _registries[key] = registry
    return registry


def drain_notices(session_id: str | None) -> list[str]:
    """Pop every queued host notice (bash-done / agent-message) for a session."""
    return get_registry(session_id).drain_notices()


def drain_pending_refine(session_id: str | None) -> dict[str, Any] | None:
    """Pop the refine request scheduled by ``refine.run`` this turn."""
    registry = get_registry(session_id)
    pending = registry.refine_pending
    registry.refine_pending = None
    return pending


def drain_pending_compact(session_id: str | None) -> dict[str, Any] | None:
    """Pop the compaction request scheduled by ``compact.run`` this turn."""
    registry = get_registry(session_id)
    pending = registry.compact_pending
    registry.compact_pending = None
    return pending


def set_refine_in_flight(session_id: str | None, in_flight: bool) -> None:
    """Let the parent mark a refinement pass as running (refine.status)."""
    get_registry(session_id).refine_in_flight = bool(in_flight)


def reset_state() -> None:
    """Drop every registry (test isolation)."""
    _registries.clear()


__all__ = [
    "ANSWER_PREVIEW_MAX_CHARS",
    "CHILD_DEPTH",
    "COLLECT_STATUSES",
    "DELETED_COLLECT_ERROR",
    "LIST_STATUSES",
    "PROGRESS_NOTE_MIN_INTERVAL_MS",
    "ChildNameUnavailable",
    "ChildRecord",
    "ChildRegistry",
    "bridge_session_id",
    "drain_notices",
    "drain_pending_compact",
    "drain_pending_refine",
    "get_registry",
    "reset_state",
    "set_refine_in_flight",
]

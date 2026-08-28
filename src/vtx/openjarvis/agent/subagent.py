"""Subagent runtime primitives for openjarvis.

Minimal typed core shared by the ``spawn`` tool and the runtime-state
inspection tool; the manager tracks statuses of background subagents.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SubagentStatus:
    """Live status snapshot of a single background subagent."""

    task_id: str = ""
    label: str = ""
    task_description: str = ""
    phase: str = "pending"
    iteration: int = 0
    started_at: float = field(default_factory=time.monotonic)
    tool_events: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, Any] | None = None
    error: str | None = None
    stop_reason: str | None = None


class SubagentManager:
    """Tracks background subagents and their statuses."""

    max_concurrent_subagents: int = 3

    def __init__(self) -> None:
        self._task_statuses: dict[str, SubagentStatus] = {}

    def get_running_count(self) -> int:
        return sum(1 for st in self._task_statuses.values() if st.phase not in ("done", "error"))

    def status(self, task_id: str) -> SubagentStatus | None:
        return self._task_statuses.get(task_id)

    async def spawn(self, task: str, **kwargs: Any) -> str:
        """Spawn a subagent; returns a human-readable result string."""
        raise NotImplementedError("SubagentManager.spawn requires a runtime backend")


__all__ = ["SubagentManager", "SubagentStatus"]

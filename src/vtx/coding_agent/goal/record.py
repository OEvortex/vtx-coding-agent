"""Goal record model (re-exported from vtx.ai.agent.goal.record)."""

from __future__ import annotations

from vtx.ai.agent.goal.record import (
    GOAL_MODES,
    GOAL_STATUSES,
    OBJECTIVE_MAX_CHARS,
    STATUS_LABELS,
    TASK_ID_RE,
    TASK_STATUSES,
    GoalRecord,
    GoalUsage,
    TaskRecord,
    clone_record,
    count_tasks,
    create_record,
    current_task,
    new_goal_id,
    normalize_task_ids,
    objective_title,
    progress_percent,
    utc_now_iso,
)

__all__ = [
    "GOAL_MODES",
    "GOAL_STATUSES",
    "OBJECTIVE_MAX_CHARS",
    "STATUS_LABELS",
    "TASK_ID_RE",
    "TASK_STATUSES",
    "GoalRecord",
    "GoalUsage",
    "TaskRecord",
    "clone_record",
    "count_tasks",
    "create_record",
    "current_task",
    "new_goal_id",
    "normalize_task_ids",
    "objective_title",
    "progress_percent",
    "utc_now_iso",
]

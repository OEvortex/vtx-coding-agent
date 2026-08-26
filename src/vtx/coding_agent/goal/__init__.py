"""Persistent goal system for vtx (port of the pi-goal-x workflow).

Goals are durable, file-backed objectives stored under ``.vtx/goals/``.
A session focuses on at most one open goal; the focused goal injects
state into each turn, schedules auto-continue checkpoint turns, and is
reviewed by an independent auditor agent before completion archives it.
"""

from __future__ import annotations

from .record import (
    GOAL_MODES,
    GOAL_STATUSES,
    TASK_STATUSES,
    GoalRecord,
    GoalUsage,
    TaskRecord,
    clone_record,
    count_tasks,
    create_record,
    current_task,
    objective_title,
    progress_percent,
)
from .service import GoalService, get_service

__all__ = [
    "GOAL_MODES",
    "GOAL_STATUSES",
    "TASK_STATUSES",
    "GoalRecord",
    "GoalService",
    "GoalUsage",
    "TaskRecord",
    "clone_record",
    "count_tasks",
    "create_record",
    "current_task",
    "get_service",
    "objective_title",
    "progress_percent",
]

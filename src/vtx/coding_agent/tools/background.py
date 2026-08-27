"""Background sub-agent task manager.

Re-exports background task management primitives from the engine harness
(:mod:`vtx.ai.agent.background`).
"""

from __future__ import annotations

from vtx.ai.agent.background import (
    BACKGROUND_NOTIFICATION_TAG,
    MAX_PERSISTED_RESULT_CHARS,
    BackgroundTaskManager,
    BackgroundTaskRecord,
    TaskStatus,
    get_manager,
    reset_manager,
    set_manager,
)

__all__ = [
    "BACKGROUND_NOTIFICATION_TAG",
    "MAX_PERSISTED_RESULT_CHARS",
    "BackgroundTaskManager",
    "BackgroundTaskRecord",
    "TaskStatus",
    "get_manager",
    "reset_manager",
    "set_manager",
]

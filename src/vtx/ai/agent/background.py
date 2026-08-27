"""Harness-side background-task notification protocol.

The literal tag spoken between a parent agent and the background
sub-agents it spawns. Lives in the harness so both the engine and any
product (e.g. the coding agent's task tooling) can share it without an
upward dependency.
"""

from typing import Any

BACKGROUND_NOTIFICATION_TAG = "vtx:background-task-completion"


def get_manager() -> Any:
    """Return the active background task manager if installed."""
    try:
        from vtx.coding_agent.tools.background import get_manager as _get_mgr

        return _get_mgr()
    except Exception:
        return None


__all__ = ["BACKGROUND_NOTIFICATION_TAG", "get_manager"]

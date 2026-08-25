"""Harness-side background-task notification protocol.

The literal tag spoken between a parent agent and the background
sub-agents it spawns. Lives in the harness so both the engine and any
product (e.g. the coding agent's task tooling) can share it without an
upward dependency.
"""

BACKGROUND_NOTIFICATION_TAG = "vtx:background-task-completion"

__all__ = ["BACKGROUND_NOTIFICATION_TAG"]

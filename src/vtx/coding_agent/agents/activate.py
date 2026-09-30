"""Back-compat alias for :mod:`vtx.ai.agent.agents.activate`."""

from __future__ import annotations

from vtx.ai.agent.agents.activate import (
    active_permission_gates,
    active_permission_mode,
    compose_active_commands,
    compose_active_tools,
)

__all__ = [
    "active_permission_gates",
    "active_permission_mode",
    "compose_active_commands",
    "compose_active_tools",
]

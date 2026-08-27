"""Switchable handoff agents (``.vtx/agent/<name>.py``).

A user-facing "agent" is a named, switchable bundle of:

* system-prompt instructions (appended or replacing the base identity)
* optional model / provider / thinking / max-turns overrides
* an optional tool allow/deny list
* agent-scoped local tools, slash commands, and permission gates
* agent-scoped extensions (Python files loaded only when this agent is active)

The discovery, loader, and registry mirror :mod:`vtx.extensions`. The
:class:`vtx.ConversationRuntime` holds an :class:`AgentRegistry` and a
single active :class:`LoadedAgent`; the active agent's tool/command set
replaces the runtime defaults when the runtime is set up.
"""

from __future__ import annotations

from .activate import (
    active_permission_gates,
    active_permission_mode,
    compose_active_commands,
    compose_active_tools,
)
from .api import AgentAPI, LoadedAgent
from .discovery import find_agent_paths
from .loader import AgentLoadError, load_agent, load_all_agents
from .registry import AgentRegistry
from .schema import AGENT_NAME_RE, MAX_AGENT_NAME_LEN, AgentDef, PermissionAction, PermissionGate

__all__ = [
    "AGENT_ACTIVATED",
    "AGENT_CHANGED",
    "AGENT_NAME_RE",
    "MAX_AGENT_NAME_LEN",
    "TOOL_GROUP_CHANGED",
    "AgentAPI",
    "AgentDef",
    "AgentLoadError",
    "AgentRegistry",
    "LoadedAgent",
    "PermissionAction",
    "PermissionGate",
    "active_permission_gates",
    "active_permission_mode",
    "compose_active_commands",
    "compose_active_tools",
    "find_agent_paths",
    "load_agent",
    "load_all_agents",
]


def __getattr__(name: str) -> str:
    if name in ("AGENT_ACTIVATED", "AGENT_CHANGED", "TOOL_GROUP_CHANGED"):
        from vtx.ai.agent import extensions as _extensions

        value = getattr(_extensions, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module 'vtx.ai.agent.agents' has no attribute {name!r}")


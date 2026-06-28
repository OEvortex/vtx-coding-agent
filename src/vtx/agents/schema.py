"""Schema for switchable handoff agents (``.vtx/agent/<name>.py``).

An agent bundles a system-prompt profile, optional model/provider overrides,
an optional tool allow/deny list, and an optional set of agent-scoped tools
and slash commands. Files define an ``AGENT = AgentDef(...)`` constant and
may optionally export ``register(api)`` for imperative side effects
(``api.local_tool(...)``, ``api.local_command(...)``, event handlers, ...).
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

# Mirrors the rules in docs/skills.md: lowercase letters, digits, hyphens;
# no leading or trailing hyphen; no consecutive hyphens.
AGENT_NAME_RE = re.compile(r"^(?!-)(?!.*--)[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
MAX_AGENT_NAME_LEN = 64

InstructionsMode = Literal["append", "replace"]
PermissionAction = Literal["allow", "deny", "prompt"]
ThinkingLevel = Literal["none", "minimal", "low", "medium", "high", "xhigh"]


class PermissionGate(BaseModel):
    """A single permission rule scoped to one agent.

    ``when`` is a small expression language. Two forms are supported:

    * ``"<arg-path> matches '<literal-substring>'"``  — substring match
    * ``"<arg-path> == '<literal-value>'"``             — exact equality

    Examples::

        {"tool": "bash", "when": "command matches 'rm -rf'",
         "action": "deny", "reason": "destructive"}
    """

    tool: str
    when: str
    action: PermissionAction
    reason: str | None = None


class AgentDef(BaseModel):
    """A switchable agent profile. The source of truth for a tab agent.

    Loaded from a ``.vtx/agent/<name>.py`` file's module-level ``AGENT``
    constant. All fields are optional except ``name`` and ``description``.
    """

    name: str
    description: str = Field(min_length=1, max_length=512)
    icon: str | None = Field(default=None, max_length=4)
    color: str | None = None

    # model / provider overrides
    model: str | None = None
    provider: str | None = None
    base_url: str | None = None
    thinking_level: ThinkingLevel | None = None
    max_turns: int | None = Field(default=None, gt=0)

    # system prompt composition
    instructions: str | None = None
    instructions_mode: InstructionsMode = "append"

    # tool surface
    tools_allow: list[str] | None = None
    tools_deny: list[str] = Field(default_factory=list)

    # permissions
    permission_mode: Literal["auto", "prompt"] | None = None
    permission_gates: list[PermissionGate] = Field(default_factory=list)

    # handoffs between agents
    handoffs: list[str] = Field(default_factory=list)
    handoff_back: bool = True

    # extra: which agent-scoped extensions to load when this agent is active
    extensions: list[str] = Field(default_factory=list)

    # misc
    output_type: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    # SDK-bridge: raw tools (callables, BaseTool, or Agent instances are
    # accepted and wrapped into the agent's local tool set at load time).
    # Useful when a profile wants to ship tools via the ``@tool``-style
    # pattern without writing a full ``register(api)``.
    tools: list[Any] | None = None

    # Per-profile skill auto-loading. Each entry is a skill name or path
    # that will be loaded when this agent becomes active.
    skills: list[str] | None = None

    # Named tool groups for intra-profile cycling. Each key is a group
    # name (e.g. "read-only", "full") and each value is a list of built-in
    # tool names present in the allow list for that group. The user can
    # cycle through groups while staying in the same agent.
    tool_groups: dict[str, list[str]] | None = None
    active_tool_group: str | None = None

    # SDK-bridge: raw tools (callables, BaseTool, or Agent instances are
    # accepted and wrapped into the agent's local tool set at load time).
    # Useful when a profile wants to ship tools via the ``@tool``-style
    # pattern without writing a full ``register(api)``.
    tools: list[Any] | None = None

    # Per-profile skill auto-loading. Each entry is a skill name or path
    # that will be loaded when this agent becomes active.
    skills: list[str] | None = None

    # Named tool groups for intra-profile cycling. Each key is a group
    # name (e.g. "read-only", "full") and each value is a list of built-in
    # tool names present in the allow list for that group. The user can
    # cycle through groups while staying in the same agent.
    tool_groups: dict[str, list[str]] | None = None
    active_tool_group: str | None = None

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        if len(value) > MAX_AGENT_NAME_LEN:
            raise ValueError(f"agent name too long (max {MAX_AGENT_NAME_LEN} chars): {value!r}")
        if not AGENT_NAME_RE.match(value):
            raise ValueError(f"invalid agent name {value!r}: must match {AGENT_NAME_RE.pattern}")
        return value

    @field_validator("tools_allow", "tools_deny")
    @classmethod
    def _strip_empty(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return [v for v in (s.strip() for s in value) if v]

    @field_validator("tool_groups")
    @classmethod
    def _validate_tool_groups(
        cls, value: dict[str, list[str]] | None
    ) -> dict[str, list[str]] | None:
        if value is None:
            return None
        out: dict[str, list[str]] = {}
        for k, v in value.items():
            if not k or not isinstance(k, str):
                raise ValueError(f"tool group name must be a non-empty string, got {k!r}")
            if not AGENT_NAME_RE.match(k):
                raise ValueError(
                    f"invalid tool group name {k!r}: must match {AGENT_NAME_RE.pattern}"
                )
            cleaned = [s.strip() for s in v if s and str(s).strip()]
            if not cleaned:
                raise ValueError(f"tool group {k!r} must contain at least one tool name")
            out[k] = cleaned
        return out

    @field_validator("skills")
    @classmethod
    def _strip_skill_empty(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return [v for v in (s.strip() for s in value) if v]

    @field_validator("active_tool_group")
    @classmethod
    def _validate_active_group(cls, value: str | None, info: Any) -> str | None:
        if value is None:
            return None
        groups = info.data.get("tool_groups") or {}
        if value not in groups:
            raise ValueError(
                f"active_tool_group={value!r} is not a key in tool_groups "
                f"(available: {sorted(groups)})"
            )
        return value


__all__ = [
    "AGENT_NAME_RE",
    "MAX_AGENT_NAME_LEN",
    "AgentDef",
    "InstructionsMode",
    "PermissionAction",
    "PermissionGate",
    "ThinkingLevel",
]

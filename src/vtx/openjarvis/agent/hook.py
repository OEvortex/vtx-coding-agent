"""Hook context for openjarvis runtimes (richer than the VTX core context)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from vtx.core.types import Message, ToolCall


@dataclass
class AgentHookContext:
    """Per-iteration state passed to iteration-scoped hooks."""

    turn: int
    iteration: int
    messages: list[Message] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[Any] = field(default_factory=list)
    tool_events: list[dict[str, Any]] = field(default_factory=list)


__all__ = ["AgentHookContext"]

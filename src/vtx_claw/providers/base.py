"""
Shim: re-exports from vtx for backwards compatibility with vtx_claw code
that previously imported from vtx_claw.providers.base.

All provider types now live in vtx. This module delegates to them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# vtx BaseProvider interface
# Same type aliases used internally
from vtx.llm.base import BaseProvider as LLMProvider

__all__ = ["GenerationSettings", "LLMProvider", "LLMResponse", "ToolCallRequest"]


# =================================================================================================
# Shim types
# =================================================================================================


@dataclass(slots=True)
class ToolCallRequest:
    """A provider-agnostic tool call request."""

    id: str
    name: str
    arguments: dict[str, Any] | str


@dataclass(slots=True)
class LLMResponse:
    """A provider-agnostic LLM response."""

    content: str | None = None
    tool_calls: list[ToolCallRequest] | None = None
    finish_reason: str = "stop"
    error_kind: str | None = None
    reasoning_content: str | None = None
    thinking_blocks: list[Any] | None = None
    usage: dict[str, int] | None = None

    @property
    def should_execute_tools(self) -> bool:
        return bool(self.tool_calls) and self.finish_reason == "tool_calls"

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


@dataclass(slots=True)
class GenerationSettings:
    """Per-run generation settings."""

    temperature: float | None = None
    max_tokens: int | None = None
    reasoning_effort: str | None = None
    thinking_budget: int | None = None
    stop: list[str] | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    top_p: float | None = None
    seed: int | None = None
    stream: bool = False
    response_format: str | None = None
    json_mode: bool = False
    metadata: dict[str, Any] | None = None

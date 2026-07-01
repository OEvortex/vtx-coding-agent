"""
Shim: re-exports from vtx for backwards compatibility with vtx_claw code
that previously imported from vtx_claw.providers.base.

All provider types now live in vtx. This module delegates to them.
"""

from __future__ import annotations

import json

from dataclasses import dataclass, field
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
    extra_content: dict[str, Any] | None = None
    provider_specific_fields: dict[str, Any] | None = None
    function_provider_specific_fields: dict[str, Any] | None = None

    def has_valid_name(self) -> bool:
        return isinstance(self.name, str) and bool(self.name)

    def to_openai_tool_call(self) -> dict[str, Any]:
        args = self.arguments
        if isinstance(args, dict):
            args = json.dumps(args)
        func: dict[str, Any] = {"name": self.name, "arguments": args}
        if self.function_provider_specific_fields:
            func["provider_specific_fields"] = self.function_provider_specific_fields
        payload: dict[str, Any] = {"id": self.id, "type": "function", "function": func}
        if self.extra_content:
            payload["extra_content"] = self.extra_content
        if self.provider_specific_fields:
            payload["provider_specific_fields"] = self.provider_specific_fields
        return payload


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

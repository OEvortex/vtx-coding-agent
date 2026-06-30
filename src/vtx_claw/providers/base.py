"""
Shim: re-exports from vtx for backwards compatibility with vtx_claw code
that previously imported from vtx_claw.providers.base.

All provider types now live in vtx. This module delegates to them.
"""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import json_repair
from loguru import logger

from vtx.config import get_last_selected, set_last_selected

# vtx BaseProvider interface
from vtx.core.types import (
    Message,
    StreamDone,
    StreamError,
    TextPart,
    ThinkPart,
    ToolCallDelta,
    ToolCallStart,
)
from vtx.llm.base import BaseProvider as LLMProvider
from vtx.llm.base import ProviderConfig

# Same type aliases used internally
from vtx.llm.base import AuthMode as _AuthMode

__all__ = ["LLMProvider", "LLMResponse", "ToolCallRequest", "GenerationSettings"]


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
    usage: dict[str, int] | None = None

    def __init__(
        self,
        content: str | None = None,
        tool_calls: list[ToolCallRequest] | None = None,
        finish_reason: str = "stop",
        error_kind: str | None = None,
        reasoning_content: str | None = None,
        usage: dict[str, int] | None = None,
    ) -> None:
        super().__init__()
        self.content = content
        self.tool_calls = tool_calls
        self.finish_reason = finish_reason
        self.error_kind = error_kind
        self.reasoning_content = reasoning_content
        self.usage = usage


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

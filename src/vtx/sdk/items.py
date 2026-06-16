"""
RunItem union — the typed events that flow out of a streamed run.

Mirrors the OpenAI Agents SDK shape but uses Vtx's existing Pydantic
``Message`` types under the hood.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from ..core.types import (
    AssistantMessage,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
)
from .approvals import ToolApprovalItem
from .items_base import RunItemBase


@dataclass
class MessageOutputItem(RunItemBase):
    """A natural-language message produced by the model."""

    raw_item: AssistantMessage
    type: Literal["message_output_item"] = "message_output_item"

    @property
    def text(self) -> str:
        parts: list[str] = []
        for part in self.raw_item.content:
            if isinstance(part, TextContent):
                parts.append(part.text)
            elif isinstance(part, ThinkingContent):
                parts.append(part.thinking)
        return "".join(parts)

    def to_input_item(self) -> dict[str, Any]:
        return self.raw_item.model_dump(exclude_none=True)


@dataclass
class ToolCallItem(RunItemBase):
    """A tool call emitted by the model."""

    raw_item: ToolCall
    type: Literal["tool_call_item"] = "tool_call_item"
    tool_name: str | None = None
    description: str | None = None

    @property
    def name(self) -> str:
        return self.tool_name or self.raw_item.name

    @property
    def call_id(self) -> str:
        return self.raw_item.id

    def to_input_item(self) -> dict[str, Any]:
        return self.raw_item.model_dump(exclude_none=True)


@dataclass
class ToolCallOutputItem(RunItemBase):
    """The result of a tool call."""

    raw_item: ToolResultMessage
    output: Any = None
    type: Literal["tool_call_output_item"] = "tool_call_output_item"

    @property
    def call_id(self) -> str:
        return self.raw_item.tool_call_id

    @property
    def tool_name(self) -> str:
        return self.raw_item.tool_name

    def to_input_item(self) -> dict[str, Any]:
        return self.raw_item.model_dump(exclude_none=True)


@dataclass
class HandoffCallItem(RunItemBase):
    """A handoff tool call that transfers control to another agent."""

    raw_item: ToolCall
    type: Literal["handoff_call_item"] = "handoff_call_item"
    target_agent_name: str = ""

    @property
    def name(self) -> str:
        return self.raw_item.name

    @property
    def call_id(self) -> str:
        return self.raw_item.id

    def to_input_item(self) -> dict[str, Any]:
        return self.raw_item.model_dump(exclude_none=True)


@dataclass
class HandoffOutputItem(RunItemBase):
    """The result of a handoff tool call (target agent's final output)."""

    raw_item: dict[str, Any]
    source_agent: Any = None  # type: ignore[type-arg]
    target_agent: Any = None  # type: ignore[type-arg]
    type: Literal["handoff_output_item"] = "handoff_output_item"

    def to_input_item(self) -> dict[str, Any]:
        return dict(self.raw_item)


@dataclass
class ReasoningItem(RunItemBase):
    """A reasoning/thinking block produced by the model."""

    raw_item: ThinkingContent
    type: Literal["reasoning_item"] = "reasoning_item"

    @property
    def text(self) -> str:
        return self.raw_item.thinking

    def to_input_item(self) -> dict[str, Any]:
        return self.raw_item.model_dump(exclude_none=True)


RunItem = (
    MessageOutputItem
    | ToolCallItem
    | ToolCallOutputItem
    | HandoffCallItem
    | HandoffOutputItem
    | ReasoningItem
    | ToolApprovalItem
)


@dataclass
class _SequenceMetadata:
    """Lightweight metadata used by items to carry SDK-specific fields."""

    agent_name: str = ""
    synthetic: bool = False


_field = field  # re-exported for type-checkers

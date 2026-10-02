"""Wire vocabulary shared by every layer: message and event types, the
provider/tool contracts, and error formatting.

This package is a leaf. It imports nothing from the rest of ``vtx``, so any
other package may depend on it without creating a cycle.
"""

from .abc import BaseProvider, BaseTool
from .errors import format_error
from .types import (
    AssistantMessage,
    ConstrainedSampling,
    FileChanges,
    ImageContent,
    Message,
    StopReason,
    StreamDone,
    StreamError,
    StreamPart,
    TextContent,
    TextPart,
    ThinkingContent,
    ThinkPart,
    ToolCall,
    ToolCallDelta,
    ToolCallStart,
    ToolDefinition,
    ToolParameter,
    ToolResult,
    ToolResultMessage,
    Usage,
    UserMessage,
)

__all__ = [
    "AssistantMessage",
    "BaseProvider",
    "BaseTool",
    "ConstrainedSampling",
    "FileChanges",
    "ImageContent",
    "Message",
    "StopReason",
    "StreamDone",
    "StreamError",
    "StreamPart",
    "TextContent",
    "TextPart",
    "ThinkPart",
    "ThinkingContent",
    "ToolCall",
    "ToolCallDelta",
    "ToolCallStart",
    "ToolDefinition",
    "ToolParameter",
    "ToolResult",
    "ToolResultMessage",
    "Usage",
    "UserMessage",
    "format_error",
]

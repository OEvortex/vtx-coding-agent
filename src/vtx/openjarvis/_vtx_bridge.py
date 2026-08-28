"""Bridge between VTX providers/streams and the openjarvis LLM runtime helpers."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from vtx.core.types import (
    AssistantMessage,
    Message,
    StreamDone,
    StreamPart,
    TextPart,
    ThinkPart,
    ToolCallDelta,
    ToolCallStart,
    ToolDefinition,
    ToolResultMessage,
    UserMessage,
)

# Map VTX StopReason values onto the claw/anthropic-style finish reasons used
# by the openjarvis runtime (LLMResponse.should_execute_tools expects
# "tool_calls").
_STOP_REASON_MAP: dict[str, str] = {
    "stop": "stop",
    "length": "max_tokens",
    "tool_use": "tool_calls",
    "error": "error",
    "interrupted": "interrupted",
    "steer": "stop",
}


def vtx_stop_reason_to_claw(stop_reason: Any) -> str:
    """Translate a VTX :class:`StopReason` into a claw-style finish reason."""
    name = getattr(stop_reason, "value", stop_reason)
    return _STOP_REASON_MAP.get(str(name), "stop")


def _to_message(m: Any) -> Message:
    if isinstance(m, (UserMessage, AssistantMessage, ToolResultMessage)):
        return m
    role = m.get("role") if isinstance(m, dict) else None
    if role == "assistant":
        return AssistantMessage(**m)
    if role == "tool":
        return ToolResultMessage(**m)
    return UserMessage(**m)


def _to_tool_def(t: Any) -> ToolDefinition:
    if isinstance(t, ToolDefinition):
        return t
    fn = t.get("function", t) if isinstance(t, dict) else {}
    return ToolDefinition(
        name=fn.get("name", ""),
        description=fn.get("description", ""),
        parameters=fn.get("parameters") or {"type": "object", "properties": {}},
    )


class _BridgeAdapter:
    """Expose ``last_usage`` the way :func:`collect_stream_to_response` expects."""

    def __init__(self, stream: Any) -> None:
        self._stream = stream

    @property
    def last_usage(self) -> dict[str, int] | None:
        usage = getattr(self._stream, "usage", None)
        if usage is None:
            return None
        if hasattr(usage, "model_dump"):
            return usage.model_dump()
        return dict(usage)


async def create_bridge_stream(
    provider: Any,
    *,
    messages: Sequence[dict[str, Any] | Message],
    model: str,
    tool_defs: Sequence[dict[str, Any]] | None = None,
    system_prompt: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    **kwargs: Any,
) -> tuple[_BridgeAdapter, Any]:
    """Start a VTX provider stream and return ``(adapter, stream)``.

    ``stream`` is an async iterator of VTX ``StreamPart`` objects; ``adapter``
    exposes ``last_usage`` for post-iteration token accounting.
    """
    typed_messages = [_to_message(m) for m in messages]
    typed_tools = [_to_tool_def(t) for t in (tool_defs or [])]
    stream = await provider.stream(
        typed_messages,
        system_prompt=system_prompt,
        tools=typed_tools or None,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return _BridgeAdapter(stream), stream


__all__ = [
    "StreamDone",
    "StreamPart",
    "TextPart",
    "ThinkPart",
    "ToolCallDelta",
    "ToolCallStart",
    "create_bridge_stream",
    "vtx_stop_reason_to_claw",
]

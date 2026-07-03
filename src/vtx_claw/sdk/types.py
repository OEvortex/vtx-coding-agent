"""Public SDK value objects and event constants."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Literal

from vtx.core.types import StopReason as VtxStopReason
from vtx.core.types import Usage as VtxUsage

type StreamEventType = Literal[
    "run.started",
    "text.delta",
    "text.completed",
    "reasoning.delta",
    "reasoning.completed",
    "tool.started",
    "tool.completed",
    "tool.failed",
    "run.completed",
    "run.failed",
]

STREAM_EVENT_RUN_STARTED: StreamEventType = "run.started"
STREAM_EVENT_TEXT_DELTA: StreamEventType = "text.delta"
STREAM_EVENT_TEXT_COMPLETED: StreamEventType = "text.completed"
STREAM_EVENT_REASONING_DELTA: StreamEventType = "reasoning.delta"
STREAM_EVENT_REASONING_COMPLETED: StreamEventType = "reasoning.completed"
STREAM_EVENT_TOOL_STARTED: StreamEventType = "tool.started"
STREAM_EVENT_TOOL_COMPLETED: StreamEventType = "tool.completed"
STREAM_EVENT_TOOL_FAILED: StreamEventType = "tool.failed"
STREAM_EVENT_RUN_COMPLETED: StreamEventType = "run.completed"
STREAM_EVENT_RUN_FAILED: StreamEventType = "run.failed"

STREAM_EVENT_TYPES: tuple[StreamEventType, ...] = (
    STREAM_EVENT_RUN_STARTED,
    STREAM_EVENT_TEXT_DELTA,
    STREAM_EVENT_TEXT_COMPLETED,
    STREAM_EVENT_REASONING_DELTA,
    STREAM_EVENT_REASONING_COMPLETED,
    STREAM_EVENT_TOOL_STARTED,
    STREAM_EVENT_TOOL_COMPLETED,
    STREAM_EVENT_TOOL_FAILED,
    STREAM_EVENT_RUN_COMPLETED,
    STREAM_EVENT_RUN_FAILED,
)


@dataclass(slots=True)
class RunResult:
    """Result of a single agent run — vtx-backed types natively."""

    content: str
    tools_used: list[str] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)
    usage: VtxUsage | None = None
    stop_reason: VtxStopReason | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class StreamEvent:
    """A typed event emitted by ``VtxClaw.stream()`` and ``RunStream``."""

    type: StreamEventType
    delta: str = ""
    content: str = ""
    result: RunResult | None = None
    name: str | None = None
    tool_call_id: str | None = None
    arguments: dict[str, Any] | None = None
    iteration: int | None = None
    resuming: bool | None = None
    usage: dict[str, int] = field(default_factory=dict)
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SessionSnapshot:
    """A durable snapshot of one vtx_claw session."""

    key: str
    messages: list[dict[str, Any]]
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str | None = None
    updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable copy of the snapshot."""
        return {
            "key": self.key,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": deepcopy(self.metadata),
            "messages": deepcopy(self.messages),
        }


@dataclass(slots=True)
class SessionInfo:
    """Compact session metadata for listings."""

    key: str
    created_at: str | None = None
    updated_at: str | None = None
    title: str = ""
    preview: str = ""
    path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable copy of the listing row."""
        return {
            "key": self.key,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "title": self.title,
            "preview": self.preview,
            "path": self.path,
        }


def snapshot_from_session(session: Any) -> SessionSnapshot:
    return SessionSnapshot(
        key=session.key,
        created_at=session.created_at.isoformat(),
        updated_at=session.updated_at.isoformat(),
        metadata=deepcopy(session.metadata),
        messages=deepcopy(session.messages),
    )


def snapshot_from_payload(payload: Mapping[str, Any]) -> SessionSnapshot:
    return SessionSnapshot(
        key=str(payload.get("key") or ""),
        created_at=payload.get("created_at"),
        updated_at=payload.get("updated_at"),
        metadata=deepcopy(dict(payload.get("metadata") or {})),
        messages=deepcopy(list(payload.get("messages") or [])),
    )


def result_from_response(response: Any, capture: Any) -> RunResult:
    """Build a RunResult from the outbound response and capture hook.

    Converts dict-based usage/stop_reason to vtx types for consistency
    with the rest of the refactored provider layer.
    """
    content = (response.content if response else None) or ""
    metadata = dict(response.metadata) if response and response.metadata else {}

    # Convert dict usage to VtxUsage if needed
    raw_usage = capture.usage or {}
    if isinstance(raw_usage, dict):
        usage = VtxUsage(
            input_tokens=raw_usage.get("prompt_tokens", 0) or raw_usage.get("input_tokens", 0),
            output_tokens=raw_usage.get("completion_tokens", 0)
            or raw_usage.get("output_tokens", 0),
            cache_read_tokens=raw_usage.get("cache_read_tokens", 0),
            cache_write_tokens=raw_usage.get("cache_write_tokens", 0),
        )
    elif isinstance(raw_usage, VtxUsage):
        usage = raw_usage
    else:
        usage = None

    # Convert string stop_reason to VtxStopReason if needed
    stop_reason = capture.stop_reason
    if isinstance(stop_reason, str):
        from vtx_claw._vtx_bridge import claw_verdict_to_vtx

        stop_reason = claw_verdict_to_vtx(stop_reason)

    return RunResult(
        content=content,
        tools_used=capture.tools_used,
        messages=capture.messages,
        usage=usage,
        stop_reason=stop_reason,
        error=capture.error,
        metadata=metadata,
    )

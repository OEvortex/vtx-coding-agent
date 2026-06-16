"""Session backends and the ``Session`` Protocol.

The SDK ships three backends out of the box:

* :class:`InMemorySession` — non-persistent, good for tests and short scripts.
* :class:`JSONLSession` — wraps Vtx's existing append-only JSONL store.
* The ``Session`` protocol — implement your own (Redis, SQLite, etc.).

The protocol matches OpenAI's ``SessionABC`` shape (5 methods).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ..session import Session as _VtxSession


@dataclass
class SessionSettings:
    """Per-run overrides for how the session's history is loaded."""

    limit: int | None = None
    """If set, only the most recent N items are returned by ``get_items``."""


@runtime_checkable
class Session(Protocol):
    """The pluggable memory interface.

    Matches the OpenAI Agents SDK ``SessionABC`` shape, with async
    methods. Custom backends (Redis, SQLite, Dapr, MongoDB, …) implement
    this protocol and pass to ``Runner.run(..., session=...)``.
    """

    session_id: str

    async def get_items(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Return the conversation history as input-item dicts."""
        ...

    async def add_items(self, items: list[dict[str, Any]]) -> None:
        """Persist a batch of new items to the session."""
        ...

    async def pop_item(self) -> dict[str, Any] | None:
        """Remove and return the most recent item, or ``None`` if empty."""
        ...

    async def clear_session(self) -> None:
        """Remove every item from the session."""
        ...


# ---------------------------------------------------------------------------
# In-memory implementation
# ---------------------------------------------------------------------------


class InMemorySession:
    """A simple, in-process session. Items are not persisted across runs."""

    def __init__(self, session_id: str | None = None) -> None:
        self.session_id = session_id or uuid.uuid4().hex[:16]
        self._items: list[dict[str, Any]] = []
        self._settings = SessionSettings()

    @property
    def session_settings(self) -> SessionSettings:
        return self._settings

    async def get_items(self, limit: int | None = None) -> list[dict[str, Any]]:
        if limit is not None:
            return [dict(item) for item in self._items[-limit:]]
        return [dict(item) for item in self._items]

    async def add_items(self, items: list[dict[str, Any]]) -> None:
        for item in items:
            self._items.append(dict(item))

    async def pop_item(self) -> dict[str, Any] | None:
        if not self._items:
            return None
        return self._items.pop()

    async def clear_session(self) -> None:
        self._items.clear()


# ---------------------------------------------------------------------------
# JSONL implementation - wraps Vtx's Session
# ---------------------------------------------------------------------------


def _message_to_input_item(message: Any) -> dict[str, Any]:
    """Translate a Vtx ``Message`` into a flat input-item dict."""
    if isinstance(message, dict):
        return dict(message)
    if hasattr(message, "model_dump"):
        return message.model_dump(exclude_none=True)
    return dict(message)


def _input_item_to_message(item: dict[str, Any]) -> Any:
    """Translate an input-item dict back into a Vtx ``Message``.

    Best-effort: the dict must already be a valid Pydantic ``Message``
    shape. If the dict was produced by a third-party provider, the
    caller is responsible for translation.
    """
    from ..core.types import (
        AssistantMessage,
        TextContent,
        ThinkingContent,
        ToolCall,
        ToolResultMessage,
        UserMessage,
    )

    role = item.get("role")
    if role == "user":
        content = item.get("content", "")
        if isinstance(content, str):
            return UserMessage(content=content)
        # Pass through for list-of-parts content; the provider will
        # accept it as-is.
        return UserMessage(content=content)
    if role == "assistant":
        content = item.get("content", [])
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        parts: list[Any] = []
        tool_calls_data = item.get("tool_calls") or []
        for part in content:
            if isinstance(part, dict):
                if part.get("type") == "text":
                    parts.append(TextContent(text=part.get("text", "")))
                elif part.get("type") in ("thinking", "reasoning"):
                    parts.append(
                        ThinkingContent(
                            thinking=part.get("thinking") or part.get("text") or "",
                            signature=part.get("signature"),
                        )
                    )
                elif part.get("type") == "tool_call":
                    arguments_raw = part.get("arguments", {})
                    if not isinstance(arguments_raw, dict):
                        arguments_raw = {}
                    parts.append(
                        ToolCall(
                            id=part.get("id", uuid.uuid4().hex[:12]),
                            name=part.get("name", ""),
                            arguments=arguments_raw,
                        )
                    )
                else:
                    parts.append(part)
            else:
                parts.append(part)
        for tc in tool_calls_data:
            parts.append(
                ToolCall(
                    id=tc.get("id", uuid.uuid4().hex[:12]),
                    name=tc.get("name", ""),
                    arguments=tc.get("arguments", {}),
                )
            )
        return AssistantMessage(content=parts, stop_reason=None)
    if role == "tool_result" or role == "tool":
        return ToolResultMessage(
            tool_call_id=item.get("tool_call_id") or item.get("tool_call") or "",
            tool_name=item.get("tool_name", ""),
            content=[TextContent(text=str(item.get("content", "")))],
            is_error=item.get("is_error", False),
        )
    # Fallback: pass through the raw dict as a user message.
    return UserMessage(content=str(item))


class JSONLSession:
    """A session backed by Vtx's append-only JSONL file store.

    The JSONL file is interoperable with Vtx's TUI/headless mode: a
    session created by the SDK can be resumed from the TUI, and vice
    versa.
    """

    def __init__(self, path: str | Path | None = None, *, session_id: str | None = None) -> None:
        self._path: Path | None = Path(path).expanduser() if path else None
        self._session: _VtxSession
        if self._path is not None and self._path.exists():
            self._session = _VtxSession.load(self._path)
        elif self._path is not None:
            # New persisted session: create a fresh in-memory session
            # and write a header on the first persist.
            from vtx.session import Session as VtxSession
            from vtx.session import SessionHeader

            self._session = VtxSession.create(
                cwd=".", persist=True, provider=None, model_id=None, thinking_level="high"
            )
            # Force the session file to the path the user supplied.
            self._session._session_file = self._path
            self._session._header = SessionHeader(
                id=self._session.id,
                timestamp=self._session._header.timestamp if self._session._header else "",
                cwd=".",
            )
            self._session._flushed = False
        else:
            self._session = _VtxSession.in_memory()

        self._cached_input_items: list[dict[str, Any]] | None = None
        self._dirty = False

    @property
    def session_id(self) -> str:
        return self._session.id

    @property
    def path(self) -> Path | None:
        return self._session.session_file

    @property
    def session_settings(self) -> SessionSettings:
        return SessionSettings()

    async def get_items(self, limit: int | None = None) -> list[dict[str, Any]]:
        if self._cached_input_items is None:
            self._cached_input_items = [_message_to_input_item(m) for m in self._session.messages]
        items = self._cached_input_items
        if limit is not None:
            items = items[-limit:]
        return [dict(item) for item in items]

    async def add_items(self, items: list[dict[str, Any]]) -> None:
        if self._cached_input_items is None:
            self._cached_input_items = await self.get_items()
        for item in items:
            self._cached_input_items.append(dict(item))
            self._session.append_message(_input_item_to_message(item))
        self._dirty = True
        if self._session.session_file is not None:
            self._session.ensure_persisted()

    async def pop_item(self) -> dict[str, Any] | None:
        if self._cached_input_items is None:
            self._cached_input_items = await self.get_items()
        if not self._cached_input_items:
            return None
        return self._cached_input_items.pop()

    async def clear_session(self) -> None:
        self._cached_input_items = []


__all__ = ["InMemorySession", "JSONLSession", "Session", "SessionSettings"]

"""Session management — VTX-backed with typed entries, compaction-aware messages.

Uses VTX's file format (header, message, compaction, custom entries) and
returns VTX Message objects (UserMessage, AssistantMessage, ToolResultMessage)
from ``session.messages``.  No separate ``history.jsonl`` — compaction is
embedded directly in the session file as ``CompactionEntry``.

Migration from the old format (``_type: "metadata"`` header + flat message
dicts) is handled automatically on first load.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from contextlib import suppress
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from vtx.core.types import (
    AssistantMessage,
    ImageContent,
    Message,
    StopReason,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from vtx.openjarvis.utils.helpers import (
    ensure_dir,
    estimate_message_tokens,
    find_legal_message_start,
    image_placeholder_text,
    recent_message_start_index,
    safe_filename,
    strip_think,
)
from vtx.openjarvis.utils.paths import get_legacy_sessions_dir

_FILE_MAX_MESSAGES = 2000
_MIN_REPLAY_MAX_MESSAGES = 120
_REPLAY_TOKENS_PER_MESSAGE = 100
_MESSAGE_TIME_PREFIX_RE = re.compile(r"^\[Message Time: [^\]]+\]\n?")
_LOCAL_IMAGE_BREADCRUMB_RE = re.compile(r"^\[image: (?:/|~)[^\]]+\]\s*$")
_TOOL_CALL_ECHO_RE = re.compile(r"^\s*(?:generate_image|message)\([^)]*\)\s*$")
_SESSION_PREVIEW_MAX_CHARS = 120
_SESSION_LIST_PREVIEW_MAX_RECORDS = 200
_SESSION_LIST_PREVIEW_MAX_CHARS = 1_000_000
_FORK_VOLATILE_METADATA_KEYS = {
    "goal_state",
    "pending_user_turn",
    "runtime_checkpoint",
    "thread_goal",
    "title",
    "title_user_edited",
}
_CURRENT_VERSION = 1


def replay_max_messages_for_context(context_window_tokens: int | None) -> int:
    if not context_window_tokens or context_window_tokens <= 0:
        return _FILE_MAX_MESSAGES
    return min(
        _FILE_MAX_MESSAGES,
        max(_MIN_REPLAY_MAX_MESSAGES, context_window_tokens // _REPLAY_TOKENS_PER_MESSAGE),
    )


def _sanitize_assistant_replay_text(content: str) -> str:
    content = _MESSAGE_TIME_PREFIX_RE.sub("", content, count=1)
    lines = [
        line
        for line in content.splitlines()
        if not _LOCAL_IMAGE_BREADCRUMB_RE.match(line) and not _TOOL_CALL_ECHO_RE.match(line)
    ]
    return "\n".join(lines).strip()


def _text_preview(content: Any) -> str:
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                value = block.get("text")
                if isinstance(value, str):
                    parts.append(value)
        text = " ".join(parts)
    else:
        return ""
    text = _sanitize_assistant_replay_text(text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > _SESSION_PREVIEW_MAX_CHARS:
        text = text[: _SESSION_PREVIEW_MAX_CHARS - 1].rstrip() + "…"
    return text


def _message_preview(message: Message) -> str:
    content = message.content
    if isinstance(content, str):
        return _text_preview(content)
    text_parts = [
        p.text if isinstance(p, TextContent) else p.thinking
        for p in content
        if isinstance(p, (TextContent, ThinkingContent))
    ]
    return _text_preview(" ".join(text_parts))


def _metadata_title(metadata: Any) -> str:
    if not isinstance(metadata, dict):
        return ""
    title = metadata.get("title")
    if not isinstance(title, str):
        return ""
    if metadata.get("title_user_edited") is True:
        return title
    return strip_think(title)


def _message_content_text(msg: Message) -> str:
    """Extract plain text from any Message type."""
    if isinstance(msg.content, str):
        return msg.content
    parts = []
    for part in msg.content:
        if isinstance(part, TextContent):
            parts.append(part.text)
        elif isinstance(part, ThinkingContent):
            parts.append(part.thinking)
    return "".join(parts)


def _message_tool_calls(msg: Message) -> list[dict[str, Any]]:
    """Return tool calls from an assistant message as dicts (compat helper)."""
    if not isinstance(msg, AssistantMessage):
        return []
    return [
        {
            "id": tc.id,
            "type": "function",
            "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
        }
        for tc in msg.content
        if isinstance(tc, ToolCall)
    ]


def _dict_to_tool_calls(d: dict[str, Any]) -> list[ToolCall]:
    """Convert openjarvis-style tool_calls list to VTX ToolCall objects."""
    result = []
    for tc in d.get("tool_calls") or []:
        if isinstance(tc, dict):
            func = tc.get("function", {})
            args = func.get("arguments", "{}")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except (json.JSONDecodeError, ValueError):
                    args = {}
            result.append(
                ToolCall(id=str(tc.get("id", "")), name=str(func.get("name", "")), arguments=args)
            )
    return result


def _message_dict_to_vtx(d: dict[str, Any]) -> Message:
    """Convert openjarvis message dict to VTX Message."""
    role = d.get("role", "")
    content = d.get("content", "")

    if role == "user":
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "image_url":
                    image_data = block.get("image_url", {}).get("url", "")
                    if image_data.startswith("data:"):
                        mime_part, _, b64 = image_data[5:].partition(";base64,")
                        parts.append(ImageContent(data=b64, mime_type=mime_part))
                    else:
                        parts.append(TextContent(text=image_data))
                elif isinstance(block, dict) and block.get("type") == "text":
                    parts.append(TextContent(text=str(block.get("text", ""))))
                else:
                    parts.append(TextContent(text=str(block)))
            return UserMessage(content=parts if parts else content)
        return UserMessage(content=str(content))

    if role == "assistant":
        parts: list[TextContent | ThinkingContent | ToolCall] = []
        if isinstance(content, str) and content:
            parts.append(TextContent(text=content))
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(TextContent(text=str(block.get("text", ""))))
                elif isinstance(block, dict):
                    parts.append(TextContent(text=str(block)))
        # reasoning
        reasoning = d.get("reasoning_content") or d.get("thinking_blocks")
        if reasoning:
            if isinstance(reasoning, str):
                parts.append(ThinkingContent(thinking=reasoning))
            elif isinstance(reasoning, list):
                for rb in reasoning:
                    if isinstance(rb, dict):
                        thinking = rb.get("thinking", rb.get("content", ""))
                        sig = rb.get("signature")
                        if isinstance(thinking, str):
                            parts.append(ThinkingContent(thinking=thinking, signature=sig))
        # tool calls
        parts.extend(_dict_to_tool_calls(d))
        return AssistantMessage(
            content=parts, stop_reason=_parse_stop_reason(d.get("stop_reason"))
        )

    if role == "tool":
        tool_call_id = str(d.get("tool_call_id", ""))
        tool_name = str(d.get("name", ""))
        if isinstance(content, str):
            text_content = content
        elif isinstance(content, list):
            text_content = " ".join(str(b.get("text", "")) for b in content if isinstance(b, dict))
        else:
            text_content = str(content or "")
        return ToolResultMessage(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            content=[TextContent(text=text_content)],
            is_error=d.get("is_error", False),
        )

    # Fallback: treat as user
    return UserMessage(content=str(content))


def _parse_stop_reason(val: Any) -> StopReason | None:
    if isinstance(val, StopReason):
        return val
    if isinstance(val, str):
        mapping = {
            "stop": StopReason.STOP,
            "length": StopReason.LENGTH,
            "tool_use": StopReason.TOOL_USE,
            "error": StopReason.ERROR,
            "interrupted": StopReason.INTERRUPTED,
            "max_iterations": StopReason.STOP,
        }
        return mapping.get(val)
    return None


def _vtx_message_to_extra(msg: Message, d: dict[str, Any]) -> dict[str, Any]:
    """Extract openjarvis-specific extra fields from a raw dict."""
    extra = {}
    for key in (
        "timestamp",
        "latency_ms",
        "injected_event",
        "subagent_task_id",
        "sender_id",
        "_command",
        "source",
        "cli_apps",
        "mcp_presets",
        "media",
        "stop_reason",
        "reasoning_content",
        "thinking_blocks",
        "_channel_delivery",
        "is_error",
    ):
        if key in d:
            extra[key] = deepcopy(d[key])
    if isinstance(msg, AssistantMessage) and d.get("tool_calls"):
        extra["tool_calls"] = deepcopy(d["tool_calls"])
    return extra


def _message_to_dict(msg: Message, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Convert VTX Message + optional extras back to openjarvis dict."""
    d: dict[str, Any] = {}
    if extra:
        d.update(extra)

    if isinstance(msg, UserMessage):
        d["role"] = "user"
        if isinstance(msg.content, str):
            d["content"] = msg.content
        else:
            blocks = []
            for part in msg.content:
                if isinstance(part, TextContent):
                    blocks.append({"type": "text", "text": part.text})
                elif isinstance(part, ImageContent):
                    blocks.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{part.mime_type};base64,{part.data}"},
                        }
                    )
            d["content"] = blocks

    elif isinstance(msg, AssistantMessage):
        d["role"] = "assistant"
        text_parts = []
        tool_calls = []
        for part in msg.content:
            if isinstance(part, TextContent):
                text_parts.append(part.text)
            elif isinstance(part, ThinkingContent):
                d.setdefault("thinking_blocks", []).append(
                    {"thinking": part.thinking, "signature": part.signature}
                )
                d["reasoning_content"] = part.thinking
            elif isinstance(part, ToolCall):
                tool_calls.append(
                    {
                        "id": part.id,
                        "type": "function",
                        "function": {"name": part.name, "arguments": json.dumps(part.arguments)},
                    }
                )
        d["content"] = "\n".join(text_parts) if text_parts else ""
        if tool_calls:
            d["tool_calls"] = tool_calls
        if msg.stop_reason:
            d["stop_reason"] = msg.stop_reason.value

    elif isinstance(msg, ToolResultMessage):
        d["role"] = "tool"
        d["tool_call_id"] = msg.tool_call_id
        d["name"] = msg.tool_name
        text_parts = [p.text for p in msg.content if isinstance(p, TextContent)]
        d["content"] = "\n".join(text_parts) if text_parts else ""
        if msg.is_error:
            d["is_error"] = True

    return d


def _generate_id() -> str:
    return uuid.uuid4().hex[:12]


def _now_iso() -> str:
    return datetime.now().isoformat()


@dataclass
class RetentionResult:
    dropped: list[dict]
    already_consolidated_count: int


class Session:
    """VTX-backed conversation session with compaction-aware messages.

    ``session.messages`` returns VTX Message objects (``UserMessage``,
    ``AssistantMessage``, ``ToolResultMessage``).  If a ``CompactionEntry``
    exists, the list is synthesized with summary messages prepended, just
    like ``vtx.session.Session.messages``.

    Agenite-claw-specific fields (e.g. ``latency_ms``, ``injected_event``)
    are stored in the entry envelope alongside the VTX message and
    roundtripped through the JSONL file.
    """

    def __init__(
        self,
        key: str,
        *,
        messages: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
        created_at: datetime | None = None,
        updated_at: datetime | None = None,
        last_consolidated: int = 0,
        compaction_summary: str | None = None,
    ):
        self.key = key
        self._metadata = metadata or {}
        self._created_at = created_at or datetime.now()
        self._updated_at = updated_at or datetime.now()
        self._last_consolidated = last_consolidated
        self._compaction_summary = compaction_summary
        # Raw entries: list of (Message, extra_dict) for messages, or
        # {"_type": "compaction", "summary": ...} for compaction entries.
        self._entries: list[dict[str, Any]] = []
        self._extra_map: dict[int, dict[str, Any]] = {}  # index in _entries -> extra
        self._dirty = False

        if messages:
            for msg_dict in messages:
                self._entries.append(msg_dict)
                self._dirty = True

    # -- properties -----------------------------------------------------------

    @property
    def messages(self) -> list[Message]:
        """Compaction-aware message list (VTX Message objects).

        If a compaction entry exists, prepends synthetic summary messages
        then returns only post-compaction messages, mirroring VTX's approach.
        """
        if self._compaction_summary:
            return [
                UserMessage(
                    content="[Context compacted — conversation history summarized above."
                    " Continue working on the task.]"
                ),
                AssistantMessage(
                    content=[TextContent(text=self._compaction_summary)],
                    stop_reason=StopReason.STOP,
                ),
                *self._vtx_messages_from_consolidated(),
            ]
        return self._vtx_messages_all()

    @property
    def all_messages(self) -> list[Message]:
        """All messages regardless of compaction."""
        return self._vtx_messages_all()

    @property
    def metadata(self) -> dict[str, Any]:
        return self._metadata

    @metadata.setter
    def metadata(self, value: dict[str, Any]) -> None:
        self._metadata = value

    @property
    def created_at(self) -> datetime:
        return self._created_at

    @property
    def updated_at(self) -> datetime:
        return self._updated_at

    @updated_at.setter
    def updated_at(self, value: datetime) -> None:
        self._updated_at = value

    @property
    def last_consolidated(self) -> int:
        """Number of entries that have been consolidated."""
        return self._last_consolidated

    @last_consolidated.setter
    def last_consolidated(self, value: int) -> None:
        self._last_consolidated = value

    @property
    def compaction_summary(self) -> str | None:
        return self._compaction_summary

    # -- internal helpers -----------------------------------------------------

    def _vtx_messages_all(self) -> list[Message]:
        result = []
        for entry in self._entries:
            if entry.get("_type") == "compaction":
                continue
            msg = _message_dict_to_vtx(entry)
            result.append(msg)
        return result

    def _vtx_messages_from_consolidated(self) -> list[Message]:
        result = []
        for entry in self._entries[self._last_consolidated :]:
            if entry.get("_type") == "compaction":
                continue
            msg = _message_dict_to_vtx(entry)
            result.append(msg)
        return result

    def _message_extra(self, idx: int) -> dict[str, Any]:
        entry = self._entries[idx]
        if entry.get("_type") == "compaction":
            return {}
        return _vtx_message_to_extra(_message_dict_to_vtx(entry), entry)

    # -- public API -----------------------------------------------------------

    def add_message(self, role: str, content: str, **kwargs: Any) -> None:
        """Append a message dict (compat method)."""
        entry = {"role": role, "content": content, "timestamp": _now_iso(), **kwargs}
        self._entries.append(entry)
        self._updated_at = datetime.now()
        self._dirty = True

    def append_vtx_message(self, msg: Message, **extra: Any) -> None:
        """Append a VTX Message object directly."""
        entry = _message_to_dict(msg, extra=extra if extra else None)
        entry.setdefault("timestamp", _now_iso())
        self._entries.append(entry)
        self._updated_at = datetime.now()
        self._dirty = True

    def extend_messages(self, message_dicts: list[dict[str, Any]]) -> None:
        """Extend with raw message dicts."""
        for d in message_dicts:
            d.setdefault("timestamp", _now_iso())
            self._entries.append(d)
        self._updated_at = datetime.now()
        self._dirty = True

    def get_history(
        self,
        max_messages: int = _FILE_MAX_MESSAGES,
        *,
        max_tokens: int = 0,
        extend_to_user: bool = False,
    ) -> list[dict[str, Any]]:
        """Return messages as dicts for LLM context, respecting compaction."""
        raw = self._entries[self._last_consolidated :]
        max_messages = max_messages if max_messages > 0 else _FILE_MAX_MESSAGES
        start_idx = recent_message_start_index(raw, max_messages, extend_to_user=extend_to_user)
        sliced = raw[start_idx:]

        for i, message in enumerate(sliced):
            if message.get("role") == "user":
                start = i
                if i > 0 and sliced[i - 1].get("_channel_delivery"):
                    start = i - 1
                sliced = sliced[start:]
                break

        start = find_legal_message_start(sliced)
        if start:
            sliced = sliced[start:]

        out: list[dict[str, Any]] = []
        for message in sliced:
            if message.get("_command"):
                continue
            content = message.get("content", "")
            role = message.get("role")
            if role == "assistant" and isinstance(content, str):
                content = _sanitize_assistant_replay_text(content)
            media = message.get("media")
            if role == "user" and isinstance(media, list) and media and isinstance(content, str):
                breadcrumbs = "\n".join(
                    image_placeholder_text(p) for p in media if isinstance(p, str) and p
                )
                content = f"{content}\n{breadcrumbs}" if content else breadcrumbs
            cli_apps = message.get("cli_apps")
            if (
                role == "user"
                and isinstance(cli_apps, list)
                and cli_apps
                and isinstance(content, str)
            ):
                cli_lines: list[str] = []
                for item in cli_apps[:8]:
                    if not isinstance(item, dict):
                        continue
                    name = str(item.get("name") or "").strip().lower()
                    if not name:
                        continue
                    entry = str(item.get("entry_point") or "unknown").strip() or "unknown"
                    cli_lines.append(
                        f"[CLI App Attachment: @{name}; tool=run_cli_app; entry_point={entry}; "
                        f"skill=skills/cli-app-{name}/SKILL.md]"
                    )
                if cli_lines:
                    breadcrumbs = "\n".join(cli_lines)
                    content = f"{content}\n{breadcrumbs}" if content else breadcrumbs
            mcp_presets = message.get("mcp_presets")
            if (
                role == "user"
                and isinstance(mcp_presets, list)
                and mcp_presets
                and isinstance(content, str)
            ):
                mcp_lines: list[str] = []
                for item in mcp_presets[:8]:
                    if not isinstance(item, dict):
                        continue
                    name = str(item.get("name") or "").strip().lower()
                    if not name:
                        continue
                    transport = str(item.get("transport") or "mcp").strip() or "mcp"
                    mcp_lines.append(
                        f"[MCP Preset Attachment: @{name}; tool_prefix=mcp_{name}_; "
                        f"transport={transport}]"
                    )
                if mcp_lines:
                    breadcrumbs = "\n".join(mcp_lines)
                    content = f"{content}\n{breadcrumbs}" if content else breadcrumbs
            if role == "assistant" and isinstance(content, str) and not content.strip():  # noqa
                if not any(
                    key in message
                    for key in ("tool_calls", "reasoning_content", "thinking_blocks")
                ):
                    continue
            entry: dict[str, Any] = {"role": message["role"], "content": content}
            for key in (
                "tool_calls",
                "tool_call_id",
                "name",
                "reasoning_content",
                "thinking_blocks",
            ):
                if key in message:
                    entry[key] = message[key]
            out.append(entry)

        if max_tokens > 0 and out:
            kept: list[dict[str, Any]] = []
            used = 0
            for message in reversed(out):
                tokens = estimate_message_tokens(message)
                if kept and used + tokens > max_tokens:
                    break
                kept.append(message)
                used += tokens
            kept.reverse()

            first_user = next((i for i, m in enumerate(kept) if m.get("role") == "user"), None)
            if first_user is not None:
                kept = kept[first_user:]
            else:
                recovered_user = next(
                    (i for i in range(len(out) - 1, -1, -1) if out[i].get("role") == "user"), None
                )
                if recovered_user is not None:
                    kept = out[recovered_user:]

            start = find_legal_message_start(kept)
            if start:
                kept = kept[start:]
            out = kept
        return out

    def clear(self) -> None:
        self._entries = []
        self._last_consolidated = 0
        self._compaction_summary = None
        self._updated_at = datetime.now()
        self._metadata.pop("_last_summary", None)
        self._dirty = True

    def retain_recent_legal_suffix(
        self, max_messages: int, *, extend_to_user: bool = False
    ) -> RetentionResult:
        if max_messages <= 0:
            dropped = list(self._entries)
            lc = self._last_consolidated
            self.clear()
            return RetentionResult(
                dropped=dropped, already_consolidated_count=min(lc, len(dropped))
            )
        if len(self._entries) <= max_messages:
            return RetentionResult(dropped=[], already_consolidated_count=0)

        original = list(self._entries)
        before_lc = self._last_consolidated

        start_idx = max(0, len(self._entries) - max_messages)
        if extend_to_user:
            start_idx = next(
                (i for i in range(start_idx, -1, -1) if self._entries[i].get("role") == "user"),
                start_idx,
            )

        retained = self._entries[start_idx:]

        first_user = next((i for i, m in enumerate(retained) if m.get("role") == "user"), None)
        if first_user is not None:
            retained = retained[first_user:]
        elif not extend_to_user:
            latest_user = next(
                (
                    i
                    for i in range(len(self._entries) - 1, -1, -1)
                    if self._entries[i].get("role") == "user"
                ),
                None,
            )
            if latest_user is not None:
                retained = self._entries[latest_user : latest_user + max_messages]

        start = find_legal_message_start(retained)
        if start:
            retained = retained[start:]

        if not extend_to_user and len(retained) > max_messages:
            retained = retained[-max_messages:]
            start = find_legal_message_start(retained)
            if start:
                retained = retained[start:]

        retained_ids = set(id(m) for m in retained)
        dropped = [m for m in original if id(m) not in retained_ids]

        already_consolidated = sum(
            1 for i, m in enumerate(original) if i < before_lc and id(m) not in retained_ids
        )

        new_lc = sum(1 for i, m in enumerate(original) if i < before_lc and id(m) in retained_ids)

        self._entries = retained
        self._last_consolidated = new_lc
        self._updated_at = datetime.now()
        self._dirty = True
        return RetentionResult(dropped=dropped, already_consolidated_count=already_consolidated)

    def enforce_file_cap(self, on_archive: Any = None, limit: int = _FILE_MAX_MESSAGES) -> None:
        if limit <= 0 or len(self._entries) <= limit:
            return

        result = self.retain_recent_legal_suffix(limit)
        if not result.dropped:
            return

        archive_chunk = result.dropped[result.already_consolidated_count :]
        if archive_chunk and on_archive:
            on_archive(archive_chunk)
        logger.info(
            "Session file cap hit for %s: dropped %d, raw-archived %d, kept %d",
            self.key,
            len(result.dropped),
            len(archive_chunk),
            len(self._entries),
        )

    def set_message_latency(self, index: int, latency_ms: int) -> None:
        """Set latency_ms on an entry by its index in ``self._entries``."""
        if 0 <= index < len(self._entries):
            self._entries[index]["latency_ms"] = latency_ms
            self._dirty = True

    @property
    def _messages_flat(self) -> list[dict[str, Any]]:
        """Return all entries as dicts (for serialisation)."""
        return list(self._entries)

    @_messages_flat.setter
    def _messages_flat(self, value: list[dict[str, Any]]) -> None:
        self._entries = list(value)

    def __len__(self) -> int:
        return len(self._entries)

    def __getitem__(self, idx: int) -> dict[str, Any]:
        return self._entries[idx]


def _migrate_entry(data: dict[str, Any]) -> dict[str, Any] | None:
    """Convert old-format metadata header or old-format message if detected.

    Returns ``None`` for entries that should be skipped (e.g. old metadata
    lines already consumed) or a normalized entry dict.
    """
    if data.get("_type") == "metadata":
        # Old header line — skip; metadata extracted by caller.
        return None
    if data.get("_type") == "compaction":
        # Already new format
        return data
    if data.get("type") == "header":
        return None
    if data.get("type") == "message":
        # VTX-format message — unwrap
        msg_data = data.get("message", {})
        extra = {
            k: v
            for k, v in data.items()
            if k not in ("type", "id", "parent_id", "timestamp", "message")
        }
        if extra:
            msg_data.update(extra)
        return msg_data
    return data  # assume it's a flat message dict


class SessionManager:
    """Manages conversation sessions — VTX-format JSONL files.

    Each session is one JSONL file with typed entries:
      - ``{"_type":"metadata","key":"cli:direct",...}`` — session header
      - ``{"role":"user",...}`` — flat message dict (used internally, compatible
        with VTX's Message types when loaded)
      - ``{"_type":"compaction","summary":"...","tokens_before":...}`` — compaction entry

    The manager maintains an in-memory cache for fast access.
    """

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.sessions_dir = ensure_dir(workspace / "sessions")
        self.legacy_sessions_dir = get_legacy_sessions_dir()
        self._cache: dict[str, Session] = {}
        self._storage_key_cache: dict[str, str] = {}

    @staticmethod
    def _storage_key(key: str) -> str:
        # Use a simple filesystem-safe encoding
        safe = key.replace(":", "_")
        safe = "".join(c if c.isalnum() or c in "._-@" else f"_{ord(c):02x}" for c in safe)
        return safe

    @staticmethod
    def _decode_storage_key(stem: str) -> str | None:
        # Best-effort reverse: can't perfectly reverse the encoding
        return stem.replace("_", ":", 1)

    def _get_session_path(self, key: str) -> Path:
        return self.sessions_dir / f"{self._storage_key(key)}.jsonl"

    def _get_legacy_session_path(self, key: str) -> Path:
        return self.legacy_sessions_dir / f"{safe_filename(key.replace(':', '_'))}.jsonl"

    def get_or_create(self, key: str) -> Session:
        if key in self._cache:
            return self._cache[key]
        session = self._load(key)
        if session is None:
            session = Session(key=key)
        self._cache[key] = session
        return session

    def _load(self, key: str) -> Session | None:
        path = self._get_session_path(key)
        if not path.exists():
            return None

        try:
            messages: list[dict[str, Any]] = []
            metadata: dict[str, Any] = {}
            created_at: datetime | None = None
            updated_at: datetime | None = None
            last_consolidated = 0
            compaction_summary: str | None = None
            migrated = False

            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    data = json.loads(line)

                    # New VTX header — normal load
                    if data.get("type") == "header":
                        created_at = _parse_iso(data.get("timestamp"))
                        metadata = data.get("claw_metadata", {})
                        last_consolidated = data.get("claw_last_consolidated", 0)
                        continue

                    # Migration: detect old openjarvis metadata header
                    if data.get("_type") == "metadata":
                        created_at = _parse_iso(data.get("created_at"))
                        updated_at = _parse_iso(data.get("updated_at"))
                        metadata = data.get("metadata", {})
                        last_consolidated = data.get("last_consolidated", 0)
                        migrated = True
                        continue

                    # Migration: unwrap VTX message envelope
                    if data.get("type") == "message":
                        msg_data = data.get("message", {})
                        extra = {
                            k: v
                            for k, v in data.items()
                            if k not in ("type", "id", "parent_id", "timestamp", "message")
                        }
                        if extra:
                            msg_data.update(extra)
                        messages.append(msg_data)
                        migrated = True
                        continue

                    # Compaction entry (new or migrated)
                    if data.get("_type") == "compaction":
                        compaction_summary = data.get("summary", "")
                        continue

                    # Flat message dict
                    messages.append(data)

            session = Session(
                key=key,
                messages=messages,
                metadata=metadata,
                created_at=created_at or datetime.now(),
                updated_at=updated_at or datetime.now(),
                last_consolidated=last_consolidated,
                compaction_summary=compaction_summary,
            )
            if migrated:
                logger.info("Migrated session %s to VTX format (%d messages)", key, len(messages))
            return session
        except Exception as e:
            logger.warning("Failed to load session %s: %s", key, e)
            repaired = self._repair(key)
            if repaired is not None:
                logger.info(
                    "Recovered session %s from corrupt file (%d messages)",
                    key,
                    len(repaired._entries),
                )
            return repaired

    def _repair(self, key: str, *, path: Path | None = None) -> Session | None:
        if path is None:
            path = self._get_session_path(key)
        if not path.exists():
            return None

        try:
            messages: list[dict[str, Any]] = []
            metadata: dict[str, Any] = {}
            created_at: datetime | None = None
            updated_at: datetime | None = None
            last_consolidated = 0
            compaction_summary: str | None = None
            skipped = 0

            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        skipped += 1
                        continue
                    if data.get("_type") == "metadata":
                        metadata = data.get("metadata", {})
                        if data.get("created_at"):
                            with suppress(ValueError, TypeError):
                                created_at = datetime.fromisoformat(data["created_at"])
                        if data.get("updated_at"):
                            with suppress(ValueError, TypeError):
                                updated_at = datetime.fromisoformat(data["updated_at"])
                        last_consolidated = data.get("last_consolidated", 0)
                    elif data.get("_type") == "compaction":
                        compaction_summary = data.get("summary", "")
                    elif data.get("type") == "header":
                        created_at = _parse_iso(data.get("timestamp"))
                        metadata = data.get("claw_metadata", {})
                        last_consolidated = data.get("claw_last_consolidated", 0)
                    elif data.get("type") == "message":
                        msg_data = data.get("message", {})
                        extra = {
                            k: v
                            for k, v in data.items()
                            if k not in ("type", "id", "parent_id", "timestamp", "message")
                        }
                        if extra:
                            msg_data.update(extra)
                        messages.append(msg_data)
                    else:
                        messages.append(data)

            if skipped:
                logger.warning("Skipped %d corrupt lines in session %s", skipped, key)
            if not messages and not metadata:
                return None

            return Session(
                key=key,
                messages=messages,
                metadata=metadata,
                created_at=created_at or datetime.now(),
                updated_at=updated_at or datetime.now(),
                last_consolidated=last_consolidated,
                compaction_summary=compaction_summary,
            )
        except Exception as e:
            logger.warning("Repair failed for session %s: %s", key, e)
            return None

    def save(self, session: Session, *, fsync: bool = False) -> None:
        path = self._get_session_path(session.key)
        tmp_path = path.with_suffix(".jsonl.tmp")

        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                header = {
                    "type": "header",
                    "version": _CURRENT_VERSION,
                    "id": _generate_id(),
                    "timestamp": _now_iso(),
                    "key": session.key,
                    "claw_metadata": session.metadata,
                    "claw_last_consolidated": session.last_consolidated,
                }
                f.write(json.dumps(header, ensure_ascii=False) + "\n")
                for entry in session._entries:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                # Ensure compaction summary is written even if not in _entries
                if session._compaction_summary and not any(
                    e.get("_type") == "compaction" for e in session._entries
                ):
                    comp_entry = {
                        "_type": "compaction",
                        "summary": session._compaction_summary,
                        "tokens_before": 0,
                    }
                    f.write(json.dumps(comp_entry, ensure_ascii=False) + "\n")
                if fsync:
                    f.flush()
                    os.fsync(f.fileno())

            os.replace(tmp_path, path)

            if fsync:
                with suppress(PermissionError):
                    fd = os.open(str(path.parent), os.O_RDONLY)
                    try:
                        os.fsync(fd)
                    finally:
                        os.close(fd)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise

        self._cache[session.key] = session

    def flush_all(self) -> int:
        flushed = 0
        for key, session in list(self._cache.items()):
            try:
                self.save(session, fsync=True)
                flushed += 1
            except Exception:
                logger.warning("Failed to flush session %s", key, exc_info=True)
        return flushed

    def invalidate(self, key: str) -> None:
        self._cache.pop(key, None)

    def delete_session(self, key: str) -> bool:
        path = self._get_session_path(key)
        self.invalidate(key)
        if path.exists():
            try:
                path.unlink()
                return True
            except OSError as e:
                logger.warning("Failed to delete session file %s: %s", path, e)
        return False

    def fork_session_before_user_index(
        self, source_key: str, target_key: str, before_user_index: int
    ) -> Session | None:
        if before_user_index < 0:
            return None
        source = self._cache.get(source_key) or self._load(source_key)
        if source is None:
            return None

        copied: list[dict[str, Any]] = []
        user_index = 0
        found_target = False
        for msg_dict in source._entries:
            if msg_dict.get("role") == "user":
                if user_index == before_user_index:
                    found_target = True
                    break
                user_index += 1
            copied.append(deepcopy(msg_dict))
        if user_index == before_user_index:
            found_target = True
        if not found_target:
            return None

        metadata = deepcopy(source.metadata)
        for key in _FORK_VOLATILE_METADATA_KEYS:
            metadata.pop(key, None)

        last_consolidated = min(source.last_consolidated, len(copied))
        if source.last_consolidated > len(copied):
            metadata.pop("_last_summary", None)
            last_consolidated = 0

        now = datetime.now()
        target = Session(
            key=target_key,
            messages=copied,
            metadata=metadata,
            created_at=now,
            updated_at=now,
            last_consolidated=last_consolidated,
            compaction_summary=source.compaction_summary,
        )
        self.save(target, fsync=True)
        return target

    def read_session_file(self, key: str) -> dict[str, Any] | None:
        path = self._get_session_path(key)
        if not path.exists():
            return None
        try:
            messages: list[dict[str, Any]] = []
            metadata: dict[str, Any] = {}
            created_at: str | None = None
            updated_at: str | None = None
            stored_key: str | None = None
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    data = json.loads(line)
                    if data.get("type") == "header":
                        stored_key = data.get("key", key)
                        created_at = data.get("timestamp")
                        metadata = data.get("claw_metadata", {})
                    elif data.get("_type") == "compaction":
                        continue
                    elif data.get("type") == "message":
                        msg_data = data.get("message", {})
                        extra = {
                            k: v
                            for k, v in data.items()
                            if k not in ("type", "id", "parent_id", "timestamp", "message")
                        }
                        if extra:
                            msg_data.update(extra)
                        messages.append(msg_data)
                    else:
                        messages.append(data)
            return {
                "key": stored_key or key,
                "created_at": created_at,
                "updated_at": updated_at,
                "metadata": metadata,
                "messages": messages,
            }
        except Exception as e:
            logger.warning("Failed to read session %s: %s", key, e)
            repaired = self._repair(key)
            if repaired is not None:
                logger.info("Recovered read-only session view %s from corrupt file", key)
                return {
                    "key": repaired.key,
                    "created_at": repaired.created_at.isoformat(),
                    "updated_at": repaired.updated_at.isoformat(),
                    "metadata": repaired.metadata,
                    "messages": repaired._entries,
                }
            return None

    def read_session_metadata(self, key: str) -> dict[str, Any] | None:
        path = self._get_session_path(key)
        if not path.exists():
            return None
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    data = json.loads(line)
                    if data.get("type") != "header":
                        continue
                    return {
                        "key": data.get("key", key),
                        "created_at": data.get("timestamp"),
                        "updated_at": None,
                        "metadata": data.get("claw_metadata", {}),
                    }
            return None
        except Exception as e:
            logger.warning("Failed to read session metadata %s: %s", key, e)
            repaired = self._repair(key)
            if repaired is not None:
                return {
                    "key": repaired.key,
                    "created_at": repaired.created_at.isoformat(),
                    "updated_at": repaired.updated_at.isoformat(),
                    "metadata": repaired.metadata,
                }
            return None

    def list_sessions(self) -> list[dict[str, Any]]:
        sessions = []

        for path in self.sessions_dir.glob("*.jsonl"):
            try:
                with open(path, encoding="utf-8") as f:
                    first_line = f.readline().strip()
                    if not first_line:
                        continue
                    data = json.loads(first_line)
                    if data.get("type") == "header":
                        key = data.get("key", path.stem)
                        metadata = data.get("claw_metadata", {})
                        title = _metadata_title(metadata)
                        created_at = data.get("timestamp")
                    elif data.get("_type") == "metadata":
                        key = data.get("key", path.stem)
                        metadata = data.get("metadata", {})
                        title = _metadata_title(metadata)
                        created_at = data.get("created_at")
                    else:
                        continue

                    preview = ""
                    fallback_preview = ""
                    scanned_records = 0
                    scanned_chars = 0
                    for line in f:
                        if not line.strip():
                            continue
                        scanned_records += 1
                        scanned_chars += len(line)
                        if (
                            scanned_records > _SESSION_LIST_PREVIEW_MAX_RECORDS
                            or scanned_chars > _SESSION_LIST_PREVIEW_MAX_CHARS
                        ):
                            break
                        item = json.loads(line)
                        if item.get("_type") in ("metadata", "compaction"):
                            continue
                        if item.get("type") == "header":
                            continue
                        if item.get("type") == "message":
                            msg_data = item.get("message", {})
                            role = msg_data.get("role")
                            content = msg_data.get("content", "")
                        else:
                            role = item.get("role")
                            content = item.get("content", "")
                        text = _text_preview(content)
                        if not text:
                            continue
                        if role == "user":
                            preview = text
                            break
                        if not fallback_preview and role == "assistant":
                            fallback_preview = text
                    preview = preview or fallback_preview
                    fallback_time = datetime.fromtimestamp(path.stat().st_mtime).isoformat()
                    sessions.append(
                        {
                            "key": key,
                            "created_at": created_at or fallback_time,
                            "updated_at": None,
                            "title": title,
                            "preview": preview,
                            "path": str(path),
                        }
                    )
            except Exception:
                continue
        return sorted(sessions, key=lambda x: x.get("created_at", ""), reverse=True)


def _parse_iso(val: str | None) -> datetime | None:
    if not val:
        return None
    try:
        return datetime.fromisoformat(val)
    except (ValueError, TypeError):
        return None

"""
Session - persistence layer for agent conversations.

Sessions are rows in ``ctx.db`` (see :mod:`ctx.core.store`): one ``session``
row per conversation (the picker's index) and one ``entry`` row per entry, in
sequence order. The old append-only JSONL shape still describes an entry:

    {"type": "header", "id": "...", "version": 1, "timestamp": "...",
     "cwd": "...", "system_prompt": "...", "tools": ["read", "apply_patch", ...]}
    {"type": "message", "id": "...", "parent_id": "...", "timestamp": "...", "message": {...}}
    ...

Each session keeps its historical ``sessions/<slug>/<ts>_<id>.jsonl`` path as
its identity, so callers that hold a path keep working, but that file is only
written by the one-way legacy import.
"""

from __future__ import annotations

import builtins
import contextlib
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from ctx.core import store
from ctx.core.paths import get_config_dir
from ctx.protocol.types import (
    AssistantMessage,
    Message,
    StopReason,
    TextContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)

CURRENT_VERSION = 1
_SKILL_TRIGGER_HEADER_RE = re.compile(r"^\[([a-z0-9-]+)\]\s*$")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class SessionHeader(BaseModel):
    type: Literal["header"] = "header"
    version: int = CURRENT_VERSION
    id: str
    timestamp: str
    cwd: str
    system_prompt: str | None = None
    tools: builtins.list[str] | None = None
    initial_thinking_level: str = "high"


class EntryBase(BaseModel):
    id: str
    parent_id: str | None
    timestamp: str


class MessageEntry(EntryBase):
    type: Literal["message"] = "message"
    message: Message


class ThinkingLevelChangeEntry(EntryBase):
    type: Literal["thinking_level_change"] = "thinking_level_change"
    thinking_level: str


class ModelChangeEntry(EntryBase):
    type: Literal["model_change"] = "model_change"
    provider: str
    model_id: str
    base_url: str | None = None


class CompactionEntry(EntryBase):
    type: Literal["compaction"] = "compaction"
    summary: str
    first_kept_entry_id: str  # Retained for compaction metadata; messages use entry order.
    tokens_before: int
    tokens_after: int | None = None
    details: dict[str, Any] | None = None


class CustomMessageEntry(EntryBase):
    type: Literal["custom_message"] = "custom_message"
    custom_type: str
    content: str
    display: bool = True
    details: dict[str, Any] | None = None


#: Custom entry whose content is model-facing rather than audit-only.
#:
#: Most custom entries (revert records, snapshots, handoff links) exist for the
#: transcript and must never reach the provider. A refinement notice was the
#: exception -- the model was meant to read it, so `Session.messages` converted
#: it back into a conversation message. Nothing writes one any more, but the
#: read path stays so a session recorded before the harness was removed still
#: shows the notices the model was given at the time.
REFINEMENT_NOTICE_CUSTOM_TYPE = "refinement_notice"

#: Custom entry charging a sub-agent's token usage to the session that
#: dispatched it. Not model-facing and not display-facing: it exists so
#: `Session.token_totals` can report what this session actually cost, which a
#: sub-agent's own session log cannot contribute.
SUBAGENT_USAGE_CUSTOM_TYPE = "subagent_usage"


class HarnessStateEntry(EntryBase):
    """Retired: the continual-harness write record.

    Kept so a session file written before the harness was removed still parses.
    An old branch will replay these entries and find them inert -- nothing reads
    the state any more. The model validates any ``{"type": "harness_state"}``
    record it meets, so deleting the class would make those sessions
    unloadable, which is a worse outcome than carrying a few dead lines.
    """

    type: Literal["harness_state"] = "harness_state"
    set: dict[str, dict[str, Any]] = field(default_factory=dict)
    delete: builtins.list[str] = field(default_factory=list)
    refinements: builtins.list[dict[str, Any]] = field(default_factory=list)


class SessionInfoEntry(EntryBase):
    type: Literal["session_info"] = "session_info"
    name: str | None = None


class LeafEntry(EntryBase):
    type: Literal["leaf"] = "leaf"
    target_id: str | None = None


class RuntimeCheckpointEntry(EntryBase):
    """Snapshot of partial in-flight turn state for cancel/resume.

    Written while a turn runs (after each tool batch) and cleared when the
    turn completes normally. If the user cancels (``/stop``) mid-turn, the
    latest active checkpoint is what resume restores — so partial assistant
    text / tool results aren't lost. Old readers ignore unknown ``type``.
    """

    type: Literal["runtime_checkpoint"] = "runtime_checkpoint"
    is_active: bool = True
    partial_content: builtins.list[dict[str, Any]] = field(default_factory=list)
    tool_results: builtins.list[dict[str, Any]] = field(default_factory=list)
    text_so_far: str = ""


SessionEntry = (
    MessageEntry
    | ThinkingLevelChangeEntry
    | ModelChangeEntry
    | CompactionEntry
    | CustomMessageEntry
    | HarnessStateEntry
    | SessionInfoEntry
    | LeafEntry
    | RuntimeCheckpointEntry
)


@dataclass
class TreeNode:
    entry: SessionEntry
    children: builtins.list[TreeNode] = field(default_factory=list)


class SessionInfo(BaseModel):
    id: str
    path: Path
    cwd: str
    name: str | None = None
    created: datetime
    modified: datetime
    message_count: int
    first_message: str
    parent_session_id: str | None = None


@dataclass(frozen=True)
class SessionTokenTotals:
    input_tokens: int = 0
    output_tokens: int = 0
    context_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
        )


ENTRY_TYPES: dict[str, type[SessionEntry]] = {}


def _entry_from_data(data: dict[str, Any]) -> SessionEntry | None:
    """Build one entry from its stored JSON, or ``None`` for unknown types."""
    if not ENTRY_TYPES:
        ENTRY_TYPES.update(
            {
                "message": MessageEntry,
                "thinking_level_change": ThinkingLevelChangeEntry,
                "model_change": ModelChangeEntry,
                "compaction": CompactionEntry,
                "custom_message": CustomMessageEntry,
                "harness_state": HarnessStateEntry,
                "session_info": SessionInfoEntry,
                "leaf": LeafEntry,
                "runtime_checkpoint": RuntimeCheckpointEntry,
            }
        )
    model = ENTRY_TYPES.get(str(data.get("type")))
    if model is None:
        return None
    try:
        return model.model_validate(data)
    except Exception:
        return None


def _parse_session_file(path: Path) -> tuple[SessionHeader | None, builtins.list[SessionEntry]]:
    """Read a legacy JSONL log.

    Only the one-way import uses this now; it exists so an old install (or a
    transcript checked into a backup) can still be loaded once.
    """
    header: SessionHeader | None = None
    entries: builtins.list[SessionEntry] = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if data.get("type") == "header":
                    with contextlib.suppress(Exception):
                        header = SessionHeader.model_validate(data)
                    continue
                entry = _entry_from_data(data)
                if entry is not None:
                    entries.append(entry)
    except OSError:
        pass
    return header, entries


def _preview(text: str) -> str:
    return Session._extract_preview_from_user_message(text)[:100]


def _message_preview(message: Message) -> str:
    content = message.content
    if isinstance(content, str):
        return _preview(content)
    if isinstance(content, builtins.list):
        for part in content:
            text = getattr(part, "text", None)
            if isinstance(text, str) and text.strip():
                return _preview(text)
    return ""


def _entry_name(entry: SessionEntry) -> str | None:
    if isinstance(entry, SessionInfoEntry) and entry.name:
        return entry.name
    return None


def _entry_parent(entry: SessionEntry) -> str | None:
    if isinstance(entry, CustomMessageEntry) and entry.custom_type == "handoff_backlink":
        details = entry.details or {}
        value = details.get("target_session_id")
        if isinstance(value, str):
            return value
    return None


@dataclass(frozen=True)
class SessionMessageCounts:
    user_messages: int = 0
    assistant_messages: int = 0
    tool_calls: int = 0
    tool_results: int = 0

    @property
    def total_messages(self) -> int:
        return self.user_messages + self.assistant_messages


class Session:
    """
    Manages conversation persistence as append-only JSONL.

    Usage:
        # Create new session with initial model/thinking level
        session = Session.create("/path/to/project", provider="openai", model_id="gpt-4")

        # Add messages
        session.append_message(user_message)
        session.append_message(assistant_message)

        # Resume later
        session = Session.load(session_file_path)
        messages = session.messages

        # List sessions
        sessions = Session.list("/path/to/project")
    """

    @staticmethod
    def generate_id() -> str:
        return uuid.uuid4().hex[:8]

    @staticmethod
    def get_sessions_dir(cwd: str) -> Path:
        safe_cwd = cwd.replace("/", "-").replace("\\", "-").strip("-")
        sessions_dir = get_config_dir() / "sessions" / safe_cwd
        sessions_dir.mkdir(parents=True, exist_ok=True)
        sessions_dir.chmod(0o700)
        return sessions_dir

    def __init__(
        self,
        session_id: str,
        cwd: str,
        session_file: Path | None = None,
        persist: bool = True,
        initial_provider: str | None = None,
        initial_model_id: str | None = None,
        initial_thinking_level: str = "high",
    ):
        self._id = session_id
        self._cwd = cwd
        self._session_file = session_file
        self._persist = persist

        # In-memory state
        self._header: SessionHeader | None = None
        self._entries: builtins.list[SessionEntry] = []
        self._by_id: dict[str, SessionEntry] = {}
        self._leaf_id: str | None = None

        # Initial settings (used as fallback when no entries exist)
        self._initial_provider = initial_provider
        self._initial_model_id = initial_model_id
        self._initial_thinking_level = initial_thinking_level

        # Track persistence state
        self._flushed = False
        self._persisted_entries_count = 0

        # Per-session facts the picker reads, kept in step with appends so
        # saving one entry does not have to recount the whole transcript.
        self._message_count = 0
        self._first_message: str | None = None
        self._name: str | None = None
        self._parent_id: str | None = None

    @property
    def id(self) -> str:
        return self._id

    @property
    def cwd(self) -> str:
        return self._cwd

    @property
    def session_file(self) -> Path | None:
        return self._session_file

    @property
    def system_prompt(self) -> str | None:
        return self._header.system_prompt if self._header else None

    def set_system_prompt(self, system_prompt: str | None) -> None:
        """Replace the cached header prompt and persist it.

        The runtime prefers the header prompt over a fresh build, so anything
        that changes what the prompt should contain — a reload picking up new
        skills, tools, or agent instructions — has to invalidate it here or the
        stale one keeps winning.
        """
        if self._header is None:
            return
        if self._header.system_prompt == system_prompt:
            return
        self._header.system_prompt = system_prompt
        self._write_all()

    def set_header_tools(self, tools: builtins.list[str] | None) -> None:
        """Record the active tool names in the header (provenance only)."""
        if self._header is None or self._header.tools == tools:
            return
        self._header.tools = tools
        self._write_all()

    @property
    def tools(self) -> builtins.list[str] | None:
        return self._header.tools if self._header else None

    @property
    def created_at(self) -> str | None:
        return self._header.timestamp if self._header else None

    @property
    def leaf_id(self) -> str | None:
        return self._leaf_id

    def _generate_entry_id(self) -> str:
        for _ in range(100):
            entry_id = self.generate_id()
            if entry_id not in self._by_id:
                return entry_id
        return uuid.uuid4().hex

    def _append_entry(self, entry: SessionEntry) -> None:
        self._entries.append(entry)
        self._by_id[entry.id] = entry
        self._leaf_id = entry.target_id if isinstance(entry, LeafEntry) else entry.id
        self._note_facts(entry)
        self._persist_entry(entry)

    def _note_facts(self, entry: SessionEntry) -> None:
        if isinstance(entry, MessageEntry):
            self._message_count += 1
            if entry.message.role == "user" and not self._first_message:
                self._first_message = _message_preview(entry.message)
        elif _entry_name(entry):
            self._name = _entry_name(entry)
        elif _entry_parent(entry):
            self._parent_id = _entry_parent(entry)

    def _persist_entry(self, entry: SessionEntry) -> None:
        if not self._persist or not self._session_file:
            return

        has_assistant = any(
            isinstance(e, MessageEntry) and e.message.role == "assistant" for e in self._entries
        ) or isinstance(entry, LeafEntry)
        # Checkpoints are durable work-in-progress and must reach disk even
        # before the first assistant message exists, otherwise a crash during
        # turn 1 loses the whole run -- the gate above used to drop them.
        # A sub-agent usage charge is the same shape of fact: accounting for
        # work already spent, so losing it under-reports the session forever.
        is_usage_charge = (
            isinstance(entry, CustomMessageEntry)
            and entry.custom_type == SUBAGENT_USAGE_CUSTOM_TYPE
        )
        if (
            not has_assistant
            and not isinstance(entry, RuntimeCheckpointEntry)
            and not is_usage_charge
        ):
            return

        # If earlier entries were skipped (e.g., pre-assistant user/custom messages),
        # rewrite to include the full sequence before appending incrementally again.
        if self._persisted_entries_count < len(self._entries) - 1:
            self._write_all()
            self._flushed = True
            self._persisted_entries_count = len(self._entries)
            return

        if not self._flushed:
            self._write_all()
            self._flushed = True
            self._persisted_entries_count = len(self._entries)
        else:
            path = str(self._session_file)
            with store.write() as conn:
                store.put_entry(path, len(self._entries) - 1, entry.model_dump(mode="json"), conn)
                store.touch_session(
                    path,
                    modified=_now_iso(),
                    message_count=self._message_count,
                    first_message=self._first_message,
                    name=self._name,
                    parent_id=self._parent_id,
                    conn=conn,
                )
            self._persisted_entries_count += 1

    def _session_meta(self) -> dict[str, Any]:
        assert self._session_file is not None
        header = self._header
        return {
            "path": str(self._session_file),
            "id": self._id,
            "cwd": self._cwd,
            "slug": self._session_file.parent.name,
            "created": header.timestamp if header else None,
            "modified": _now_iso(),
            "name": self._name,
            "parent_id": self._parent_id,
            "first_message": self._first_message,
            "message_count": self._message_count,
            "system_prompt": header.system_prompt if header else None,
            "tools": json.dumps(header.tools) if header and header.tools else None,
            "initial_thinking_level": header.initial_thinking_level if header else None,
        }

    def _recount(self) -> None:
        """Recompute the picker facts from the entries in memory."""
        self._message_count = 0
        self._first_message = None
        self._name = None
        self._parent_id = None
        for entry in self._entries:
            self._note_facts(entry)

    def _write_all(self) -> None:
        if not self._session_file:
            return
        self._recount()
        path = str(self._session_file)
        rows = [(seq, entry.model_dump(mode="json")) for seq, entry in enumerate(self._entries)]
        with store.write() as conn:
            store.put_session(self._session_meta(), conn)
            for seq, row in rows:
                store.put_entry(path, seq, row, conn)
            # Entries can shrink when a branch is dropped; the rows past the
            # end would otherwise keep a deleted tail alive.
            conn.execute(
                "DELETE FROM entry WHERE session_path = ? AND seq >= ?", (path, len(rows))
            )

    def ensure_persisted(self) -> None:
        if not self._persist or not self._session_file:
            return
        self._write_all()
        self._flushed = True
        self._persisted_entries_count = len(self._entries)

    def append_message(self, message: Message) -> str:
        entry = MessageEntry(
            id=self._generate_entry_id(),
            parent_id=self._leaf_id,
            timestamp=_now_iso(),
            message=message,
        )
        self._append_entry(entry)
        return entry.id

    def replace_message(self, entry_id: str, message: Message) -> None:
        """Swap the message on an existing :class:`MessageEntry`, in place.

        The session log is append-only, so this does not add an entry; it
        rewrites the one named by *entry_id*. Used for context slots that hold
        regenerable state — the skills catalog — where a fresh value should
        replace the stale one rather than stack a second copy behind it. A crash
        before the next rewrite leaves the previous value on disk, which is
        recoverable: the slot is keyed by a state fingerprint, so a stale copy is
        detected on resume and re-delivered.

        Raises ``ValueError`` when the entry is missing or is not a message.
        """
        entry = self._by_id.get(entry_id)
        if not isinstance(entry, MessageEntry):
            raise ValueError(f"No message entry with id {entry_id!r}")
        entry.message = message
        # Force the next persist to rewrite the whole log: an appended-only flush
        # would leave the superseded text on disk.
        self._flushed = False

    def append_thinking_level_change(self, thinking_level: str) -> str:
        entry = ThinkingLevelChangeEntry(
            id=self._generate_entry_id(),
            parent_id=self._leaf_id,
            timestamp=_now_iso(),
            thinking_level=thinking_level,
        )
        self._append_entry(entry)
        return entry.id

    def append_model_change(
        self, provider: str, model_id: str, base_url: str | None = None
    ) -> str:
        entry = ModelChangeEntry(
            id=self._generate_entry_id(),
            parent_id=self._leaf_id,
            timestamp=_now_iso(),
            provider=provider,
            model_id=model_id,
            base_url=base_url,
        )
        self._append_entry(entry)
        return entry.id

    def append_compaction(
        self,
        summary: str,
        first_kept_entry_id: str,
        tokens_before: int,
        tokens_after: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> str:
        entry = CompactionEntry(
            id=self._generate_entry_id(),
            parent_id=self._leaf_id,
            timestamp=_now_iso(),
            summary=summary,
            first_kept_entry_id=first_kept_entry_id,
            tokens_before=tokens_before,
            tokens_after=tokens_after,
            details=details,
        )
        self._append_entry(entry)
        return entry.id

    def append_custom_message(
        self,
        custom_type: str,
        content: str,
        display: bool = True,
        details: dict[str, Any] | None = None,
    ) -> str:
        entry = CustomMessageEntry(
            id=self._generate_entry_id(),
            parent_id=self._leaf_id,
            timestamp=_now_iso(),
            custom_type=custom_type,
            content=content,
            display=display,
            details=details,
        )
        self._append_entry(entry)
        return entry.id

    def append_session_info(self, name: str) -> str:
        entry = SessionInfoEntry(
            id=self._generate_entry_id(), parent_id=self._leaf_id, timestamp=_now_iso(), name=name
        )
        self._append_entry(entry)
        return entry.id

    def set_name(self, name: str) -> None:
        """Set the session name by appending a SessionInfoEntry."""
        self.append_session_info(name)

    def append_runtime_checkpoint(
        self,
        partial_content: builtins.list[dict[str, Any]],
        tool_results: builtins.list[dict[str, Any]],
        text_so_far: str = "",
    ) -> str:
        """Record partial in-flight turn state for cancel/resume.

        Marks any prior checkpoint inactive so the latest active one wins on
        resume. Not a ``MessageEntry``, so it never pollutes ``messages``.
        """
        # Deactivate any previously-active checkpoint on this branch.
        for entry in self.active_entries:
            if isinstance(entry, RuntimeCheckpointEntry) and entry.is_active:
                entry.is_active = False
        entry = RuntimeCheckpointEntry(
            id=self._generate_entry_id(),
            parent_id=self._leaf_id,
            timestamp=_now_iso(),
            partial_content=list(partial_content),
            tool_results=list(tool_results),
            text_so_far=text_so_far,
        )
        self._append_entry(entry)
        return entry.id

    def clear_runtime_checkpoint(self) -> None:
        """Drop active checkpoints once a turn completes normally.

        Appends an inactive tombstone instead of flipping the flag in place.
        The JSONL log is append-only, so an in-place mutation would never be
        written back and a reopened session would still see the checkpoint as
        active and spuriously resume a run that actually finished.
        """
        if not any(
            isinstance(e, RuntimeCheckpointEntry) and e.is_active for e in self.active_entries
        ):
            return
        self._append_entry(
            RuntimeCheckpointEntry(
                id=self._generate_entry_id(),
                parent_id=self._leaf_id,
                timestamp=_now_iso(),
                is_active=False,
            )
        )

    def load_runtime_checkpoint(self) -> RuntimeCheckpointEntry | None:
        """Return the latest runtime checkpoint, if it is still active.

        Only the newest checkpoint is consulted: an inactive one is a
        tombstone written by :meth:`clear_runtime_checkpoint` when a turn
        completed normally.
        """
        latest: RuntimeCheckpointEntry | None = None
        for entry in self.active_entries:
            if isinstance(entry, RuntimeCheckpointEntry):
                latest = entry
        return latest if latest is not None and latest.is_active else None

    def move_to(self, entry_id: str | None) -> None:
        if entry_id is not None and entry_id not in self._by_id:
            raise ValueError(f"Entry not found: {entry_id}")
        entry = LeafEntry(
            id=self._generate_entry_id(),
            parent_id=self._leaf_id,
            timestamp=_now_iso(),
            target_id=entry_id,
        )
        self._append_entry(entry)

    @property
    def entries(self) -> builtins.list[SessionEntry]:
        return self.active_entries

    @property
    def all_entries(self) -> builtins.list[SessionEntry]:
        return list(self._entries)

    def get_branch(self, leaf_id: str | None = None) -> builtins.list[SessionEntry]:
        path: builtins.list[SessionEntry] = []
        current_id = self._leaf_id if leaf_id is None else leaf_id
        while current_id:
            entry = self._by_id.get(current_id)
            if entry is None:
                break
            path.append(entry)
            current_id = entry.parent_id
        path.reverse()
        return path

    @property
    def active_entries(self) -> builtins.list[SessionEntry]:
        return self.get_branch()

    def get_tree(self) -> builtins.list[TreeNode]:
        tree_entries = [entry for entry in self._entries if not isinstance(entry, LeafEntry)]
        nodes = {entry.id: TreeNode(entry=entry) for entry in tree_entries}
        roots: builtins.list[TreeNode] = []
        for entry in tree_entries:
            node = nodes[entry.id]
            if entry.parent_id is None or entry.parent_id not in nodes:
                roots.append(node)
            else:
                nodes[entry.parent_id].children.append(node)
        for node in nodes.values():
            node.children.sort(key=lambda child: child.entry.timestamp)
        roots.sort(key=lambda node: node.entry.timestamp)
        return roots

    def get_entry(self, entry_id: str) -> SessionEntry | None:
        return self._by_id.get(entry_id)

    @staticmethod
    def _llm_message(entry: SessionEntry) -> Message | None:
        """The provider-facing message for one entry, or None if it is not one.

        A refinement notice is stored as a typed custom entry so the transcript
        can tell it from a user turn, but it carries text the model must read,
        so it is converted back here rather than dropped with the audit entries.
        """
        if isinstance(entry, MessageEntry):
            return entry.message
        if (
            isinstance(entry, CustomMessageEntry)
            and entry.custom_type == REFINEMENT_NOTICE_CUSTOM_TYPE
        ):
            return UserMessage(content=entry.content)
        return None

    @property
    def messages(self) -> builtins.list[Message]:
        """Messages for LLM context. If compaction exists, returns compacted view."""
        last_compaction: CompactionEntry | None = None
        for entry in reversed(self.active_entries):
            if isinstance(entry, CompactionEntry):
                last_compaction = entry
                break

        if last_compaction is None:
            return [
                message
                for message in (self._llm_message(e) for e in self.active_entries)
                if message is not None
            ]

        # Build compacted message list:
        # 1. Synthetic user message framing this as a state restoration
        # 2. Assistant message with the compaction summary
        # 3. All MessageEntry entries after the compaction entry

        summary_text = last_compaction.summary

        result: builtins.list[Message] = [
            UserMessage(
                content=(
                    "[Context compacted — conversation history summarized above."
                    " Continue working on the task.]"
                )
            ),
            AssistantMessage(
                content=[TextContent(text=summary_text)], stop_reason=StopReason.STOP
            ),
        ]

        # Find the compaction entry's position and include messages after it
        past_compaction = False
        for entry in self.active_entries:
            if isinstance(entry, CompactionEntry) and entry.id == last_compaction.id:
                past_compaction = True
                continue
            if not past_compaction:
                continue
            message = self._llm_message(entry)
            if message is not None:
                result.append(message)

        return result

    @property
    def all_messages(self) -> builtins.list[Message]:
        """All messages regardless of compaction (for UI rendering)."""
        return [
            message
            for message in (self._llm_message(e) for e in self.active_entries)
            if message is not None
        ]

    def get_last_assistant_text(self) -> str | None:
        for message in reversed(self.messages):
            if not isinstance(message, AssistantMessage):
                continue
            if message.stop_reason == StopReason.INTERRUPTED and not message.content:
                continue

            text = "".join(
                part.text for part in message.content if isinstance(part, TextContent)
            ).strip()
            return text or None

        return None

    def record_subagent_usage(
        self,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        turns: int = 0,
        task_id: str | None = None,
        label: str | None = None,
    ) -> None:
        """Charge a completed sub-agent's token usage to this session.

        A sub-agent runs in its own persisted session, so its assistant
        messages - and therefore its usage - never land in this transcript.
        `token_totals` reads only this session's entries, so without this the
        session's own cost would silently exclude every sub-agent it dispatched.

        The sub-agent's own session log still carries the per-message breakdown,
        and `aggregate_usage` reads that file separately, so this record does not
        double-count the lifetime total - it only makes the per-session figure
        honest. Turns are recorded for the transcript but deliberately not fed
        back into `message_counts`, which reports the model's own turns.
        """
        if not any((input_tokens, output_tokens, cache_read_tokens, cache_write_tokens)):
            return
        entry = CustomMessageEntry(
            id=self._generate_entry_id(),
            parent_id=self._leaf_id,
            timestamp=_now_iso(),
            custom_type=SUBAGENT_USAGE_CUSTOM_TYPE,
            content=label or "sub-agent",
            display=False,
            details={
                "input_tokens": int(input_tokens),
                "output_tokens": int(output_tokens),
                "cache_read_tokens": int(cache_read_tokens),
                "cache_write_tokens": int(cache_write_tokens),
                "turns": int(turns),
                "task_id": task_id,
            },
        )
        self._append_entry(entry)

    def token_totals(self) -> SessionTokenTotals:
        input_tokens = 0
        output_tokens = 0
        cache_read_tokens = 0
        cache_write_tokens = 0

        for entry in self.active_entries:
            if isinstance(entry, MessageEntry) and isinstance(entry.message, AssistantMessage):
                usage = entry.message.usage
                if usage is not None:
                    input_tokens += usage.input_tokens
                    output_tokens += usage.output_tokens
                    cache_read_tokens += usage.cache_read_tokens
                    cache_write_tokens += usage.cache_write_tokens
            elif (
                isinstance(entry, CustomMessageEntry)
                and entry.custom_type == SUBAGENT_USAGE_CUSTOM_TYPE
            ):
                # A sub-agent runs in its own session, so its tokens never
                # appear as an assistant message here. `record_subagent_usage`
                # charges them to this session instead, or `/usage` would
                # report this session as if the sub-agent had cost nothing.
                details = entry.details or {}
                input_tokens += int(details.get("input_tokens") or 0)
                output_tokens += int(details.get("output_tokens") or 0)
                cache_read_tokens += int(details.get("cache_read_tokens") or 0)
                cache_write_tokens += int(details.get("cache_write_tokens") or 0)

        # For context_tokens, find the last compaction entry
        last_compaction: CompactionEntry | None = None
        for entry in reversed(self.active_entries):
            if isinstance(entry, CompactionEntry):
                last_compaction = entry
                break

        if last_compaction is None:
            context_tokens = 0
            for entry in self.active_entries:
                if isinstance(entry, MessageEntry) and isinstance(entry.message, AssistantMessage):
                    usage = entry.message.usage
                    if usage is not None:
                        context_tokens = max(
                            context_tokens,
                            (
                                usage.input_tokens
                                + usage.output_tokens
                                + usage.cache_read_tokens
                                + usage.cache_write_tokens
                            ),
                        )
        else:
            post_compaction_usages = []
            past_compaction = False
            for entry in self.active_entries:
                if isinstance(entry, CompactionEntry) and entry.id == last_compaction.id:
                    past_compaction = True
                    continue
                if (
                    past_compaction
                    and isinstance(entry, MessageEntry)
                    and isinstance(entry.message, AssistantMessage)
                    and entry.message.usage is not None
                ):
                    post_compaction_usages.append(entry.message.usage)

            if post_compaction_usages:
                context_tokens = max(
                    (u.input_tokens + u.output_tokens + u.cache_read_tokens + u.cache_write_tokens)
                    for u in post_compaction_usages
                )
            else:
                # Estimate from active messages (self.messages)
                context_tokens = 0
                for msg in self.messages:
                    if isinstance(msg, UserMessage):
                        if isinstance(msg.content, str):
                            context_tokens += len(msg.content) // 4
                        elif isinstance(msg.content, list):
                            for part in msg.content:
                                text = getattr(part, "text", None)
                                if isinstance(text, str):
                                    context_tokens += len(text) // 4
                    elif isinstance(msg, AssistantMessage):
                        for part in msg.content:
                            text = getattr(part, "text", None)
                            if isinstance(text, str):
                                context_tokens += len(text) // 4
                            else:
                                thinking = getattr(part, "thinking", None)
                                if isinstance(thinking, str):
                                    context_tokens += len(thinking) // 4
                    elif isinstance(msg, ToolResultMessage):
                        for part in msg.content:
                            text = getattr(part, "text", None)
                            if isinstance(text, str):
                                context_tokens += len(text) // 4

        return SessionTokenTotals(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            context_tokens=context_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
        )

    def file_changes_summary(self) -> dict[str, tuple[int, int]]:
        file_changes: dict[str, tuple[int, int]] = {}
        for entry in self.active_entries:
            if isinstance(entry, MessageEntry) and isinstance(entry.message, ToolResultMessage):
                for fc in entry.message.file_changes or []:
                    prev_added, prev_removed = file_changes.get(fc.path, (0, 0))
                    file_changes[fc.path] = (prev_added + fc.added, prev_removed + fc.removed)
        return file_changes

    def message_counts(self) -> SessionMessageCounts:
        user_messages = 0
        assistant_messages = 0
        tool_calls = 0
        tool_results = 0

        for entry in self.active_entries:
            if not isinstance(entry, MessageEntry):
                continue
            message = entry.message
            if isinstance(message, UserMessage):
                user_messages += 1
            elif isinstance(message, AssistantMessage):
                assistant_messages += 1
                tool_calls += sum(1 for part in message.content if isinstance(part, ToolCall))
            elif isinstance(message, ToolResultMessage):
                tool_results += 1

        return SessionMessageCounts(
            user_messages=user_messages,
            assistant_messages=assistant_messages,
            tool_calls=tool_calls,
            tool_results=tool_results,
        )

    @property
    def name(self) -> str | None:
        for entry in reversed(self.active_entries):
            if isinstance(entry, SessionInfoEntry) and entry.name:
                return entry.name
        return None

    @property
    def thinking_level(self) -> str:
        for entry in reversed(self.active_entries):
            if isinstance(entry, ThinkingLevelChangeEntry):
                return entry.thinking_level
        return self._initial_thinking_level

    @property
    def model(self) -> tuple[str, str, str | None] | None:
        for entry in reversed(self.active_entries):
            if isinstance(entry, ModelChangeEntry):
                return (entry.provider, entry.model_id, entry.base_url)

        if self._initial_provider and self._initial_model_id:
            return (self._initial_provider, self._initial_model_id, None)
        return None

    def set_model(self, provider: str, model_id: str, base_url: str | None = None) -> None:
        current = self.model
        if (
            current
            and current[0] == provider
            and current[1] == model_id
            and current[2] == base_url
        ):
            return
        self.append_model_change(provider, model_id, base_url)

    def set_thinking_level(self, thinking_level: str) -> None:
        if self.thinking_level == thinking_level:
            return
        self.append_thinking_level_change(thinking_level)

    @classmethod
    def create(
        cls,
        cwd: str,
        persist: bool = True,
        provider: str | None = None,
        model_id: str | None = None,
        thinking_level: str = "high",
        system_prompt: str | None = None,
        tools: builtins.list[str] | None = None,
    ) -> Session:
        session_id = str(uuid.uuid4())
        timestamp = _now_iso()

        session = cls(
            session_id=session_id,
            cwd=cwd,
            persist=persist,
            initial_provider=provider,
            initial_model_id=model_id,
            initial_thinking_level=thinking_level,
        )
        session._header = SessionHeader(
            id=session_id,
            timestamp=timestamp,
            cwd=cwd,
            system_prompt=system_prompt,
            tools=tools,
            initial_thinking_level=thinking_level,
        )

        if persist:
            file_timestamp = datetime.fromisoformat(timestamp).strftime("%Y-%m-%dT%H-%M-%S")
            sessions_dir = cls.get_sessions_dir(cwd)
            session._session_file = sessions_dir / f"{file_timestamp}_{session_id}.jsonl"

        return session

    @classmethod
    def load(cls, path: Path | str) -> Session:
        path = Path(path)
        meta = store.session_meta(str(path))
        if meta is None:
            # Not migrated yet: an old install, a restored backup, or a log
            # written by a build that predates the database.
            if not path.exists():
                raise FileNotFoundError(f"Session not found: {path}")
            import_session_file(path)
            meta = store.session_meta(str(path))
            if meta is None:
                raise ValueError(f"Invalid session (no header): {path}")

        header = SessionHeader(
            id=meta["id"],
            timestamp=meta["created"] or _now_iso(),
            cwd=meta["cwd"],
            system_prompt=meta["system_prompt"],
            tools=json.loads(meta["tools"]) if meta.get("tools") else None,
            initial_thinking_level=meta.get("initial_thinking_level") or "high",
        )
        entries: builtins.list[SessionEntry] = []
        for data in store.session_entries(str(path)):
            entry = _entry_from_data(data)
            if entry is not None:
                entries.append(entry)

        session = cls(
            session_id=header.id,
            cwd=header.cwd,
            session_file=path,
            persist=True,
            initial_thinking_level=header.initial_thinking_level,
        )
        session._header = header
        session._entries = entries
        session._by_id = {e.id: e for e in entries}
        session._leaf_id = None
        for entry in entries:
            session._leaf_id = entry.target_id if isinstance(entry, LeafEntry) else entry.id
        # Already recorded, so the next append only has to write itself.
        session._flushed = True
        session._persisted_entries_count = len(entries)
        session._recount()
        return session

    @classmethod
    def continue_recent(
        cls,
        cwd: str,
        provider: str | None = None,
        model_id: str | None = None,
        thinking_level: str = "high",
        system_prompt: str | None = None,
    ) -> Session:
        sessions_dir = cls.get_sessions_dir(cwd)
        import_legacy_sessions(sessions_dir)
        most_recent = store.recent_session(sessions_dir.name)
        if most_recent is None:
            return cls.create(
                cwd,
                provider=provider,
                model_id=model_id,
                thinking_level=thinking_level,
                system_prompt=system_prompt,
            )
        return cls.load(most_recent["path"])

    @classmethod
    def continue_by_id(cls, cwd: str, session_id: str) -> Session:
        normalized_id = session_id.strip().lower()
        if not normalized_id:
            raise ValueError("Session ID cannot be empty")

        sessions_dir = cls.get_sessions_dir(cwd)
        import_legacy_sessions(sessions_dir)
        matches = store.find_sessions_by_id(sessions_dir.name, normalized_id)
        if len(matches) == 1:
            return cls.load(matches[0]["path"])
        if len(matches) > 1:
            raise ValueError(f"Session ID prefix is ambiguous: {session_id}")

        raise FileNotFoundError(f"Session not found: {session_id}")

    @classmethod
    def list(cls, cwd: str) -> builtins.list[SessionInfo]:
        sessions_dir = cls.get_sessions_dir(cwd)
        import_legacy_sessions(sessions_dir)
        sessions: builtins.list[SessionInfo] = []
        for row in store.list_sessions(sessions_dir.name):
            info = _info_from_row(row)
            if info is not None:
                sessions.append(info)
        return sessions

    @classmethod
    def list_all(cls) -> builtins.list[SessionInfo]:
        """Every session in every project folder, newest first."""
        root = get_config_dir() / "sessions"
        if root.is_dir():
            for bucket in sorted(root.iterdir()):
                if bucket.is_dir():
                    with contextlib.suppress(Exception):
                        import_legacy_sessions(bucket)
        infos = (_info_from_row(row) for row in store.list_all_sessions())
        return [info for info in infos if info is not None]

    @staticmethod
    def _extract_preview_from_user_message(content: str) -> str:
        text = content.strip()
        if not text:
            return ""

        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return text

        header_match = _SKILL_TRIGGER_HEADER_RE.match(lines[0])
        if not header_match:
            return text

        skill_name = header_match.group(1)
        query_marker_index = next(
            (i for i, line in enumerate(lines[1:], start=1) if line.lower() == "[query]"), -1
        )
        if query_marker_index == -1:
            return f"/{skill_name}"

        query = " ".join(lines[query_marker_index + 1 :]).strip()
        if not query:
            return f"/{skill_name}"

        return f"/{skill_name} {query}"

    @classmethod
    def build_session_info(cls, path: Path) -> SessionInfo | None:
        """One session's summary, straight from the row the picker sorts by."""
        row = store.session_meta(str(path))
        if row is None:
            if not Path(path).exists():
                return None
            import_session_file(Path(path))
            row = store.session_meta(str(path))
        return _info_from_row(row) if row else None

    @classmethod
    def in_memory(
        cls,
        cwd: str = ".",
        provider: str | None = None,
        model_id: str | None = None,
        thinking_level: str = "high",
        system_prompt: str | None = None,
        tools: builtins.list[str] | None = None,
    ) -> Session:
        return cls.create(
            cwd,
            persist=False,
            provider=provider,
            model_id=model_id,
            thinking_level=thinking_level,
            system_prompt=system_prompt,
            tools=tools,
        )


def _info_from_row(row: dict[str, Any]) -> SessionInfo | None:
    try:
        created = datetime.fromisoformat(row["created"]) if row.get("created") else None
        modified = datetime.fromisoformat(row["modified"]) if row.get("modified") else created
    except (TypeError, ValueError):
        return None
    if created is None or modified is None:
        return None
    return SessionInfo(
        id=row["id"],
        path=Path(row["path"]),
        cwd=row["cwd"],
        name=row.get("name"),
        created=created,
        modified=modified,
        message_count=int(row.get("message_count") or 0),
        first_message=row.get("first_message") or "(no messages)",
        parent_session_id=row.get("parent_id"),
    )


def _import_meta(
    path: Path, header: SessionHeader, entries: builtins.list[SessionEntry]
) -> dict[str, Any]:
    message_count = 0
    first_message: str | None = None
    name: str | None = None
    parent_id: str | None = None
    for entry in entries:
        if isinstance(entry, MessageEntry):
            message_count += 1
            if entry.message.role == "user" and not first_message:
                first_message = _message_preview(entry.message)
        name = _entry_name(entry) or name
        parent_id = _entry_parent(entry) or parent_id
    modified = entries[-1].timestamp if entries else header.timestamp
    return {
        "path": str(path),
        "id": header.id,
        "cwd": header.cwd,
        "slug": path.parent.name,
        "created": header.timestamp,
        "modified": modified,
        "name": name,
        "parent_id": parent_id,
        "first_message": first_message,
        "message_count": message_count,
        "system_prompt": header.system_prompt,
        "tools": json.dumps(header.tools) if header.tools else None,
        "initial_thinking_level": header.initial_thinking_level,
    }


def import_session_file(path: Path) -> bool:
    """Move one legacy JSONL log into the database, then park the file.

    Returns ``False`` when the log is already known or cannot be parsed. The
    file is renamed to ``*.imported`` rather than deleted: it is the only copy
    of a transcript an older build could still read, and renaming costs nothing.
    """
    where = str(path)
    if store.session_meta(where) is not None:
        return False
    header, entries = _parse_session_file(path)
    if header is None:
        return False
    meta = _import_meta(path, header, entries)
    # Live sessions are counted from their entries, so any ledger row this
    # file already contributed becomes a duplicate the moment it imports.
    root = path.parent.parent
    try:
        ledger_key = path.relative_to(root).as_posix()
    except ValueError:
        ledger_key = path.as_posix()
    with store.write() as conn:
        store.put_session(meta, conn)
        for seq, entry in enumerate(entries):
            store.put_entry(where, seq, entry.model_dump(mode="json"), conn)
        conn.execute("DELETE FROM usage_day WHERE file_key = ?", (ledger_key,))
        conn.execute("DELETE FROM usage_file WHERE key = ?", (ledger_key,))
    with contextlib.suppress(OSError):
        path.rename(path.with_name(path.name + ".imported"))
    return True


def import_legacy_sessions(sessions_dir: Path) -> None:
    """Import one project folder's logs the first time anything reads it."""
    flag = f"sessions_imported:{sessions_dir.name}"
    if store.get_meta(flag) == "1":
        return
    for path in sorted(sessions_dir.glob("*.jsonl")):
        with contextlib.suppress(Exception):
            import_session_file(path)
    store.set_meta(flag, "1")

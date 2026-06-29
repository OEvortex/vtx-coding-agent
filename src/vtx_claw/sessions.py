"""Session management for vtx_claw — thin wrapper around :class:`vtx.session.Session`.

vtx's Session uses JSONL persistence with tree branching, model tracking,
compaction, and goal state. This module provides a simpler lookup layer
(mapping channel:user_id → session) on top of it.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from vtx.session import Session as VtxSession

logger = logging.getLogger(__name__)


@dataclass
class SessionEntry:
    """Lightweight message entry for API consumers (WebSocket, REST)."""

    role: str
    content: str
    timestamp: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Session:
    """Thin wrapper around :class:`vtx.session.Session` for gateway use.

    Provides a simpler interface for channel ↔ session mapping while
    delegating persistence to vtx's robust JSONL session store.
    """

    session_id: str
    channel: str
    user_id: str
    messages: list[SessionEntry] = field(default_factory=list)
    created_at: float = 0.0
    last_active: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
    _vtx_session: VtxSession | None = None

    def add_message(self, role: str, content: str, **metadata: Any) -> SessionEntry:
        ts = __import__("time").time()
        entry = SessionEntry(role=role, content=content, timestamp=ts, metadata=metadata)
        self.messages.append(entry)
        self.last_active = ts
        return entry

    def get_history(self, limit: int = 50) -> list[dict[str, str]]:
        recent = self.messages[-limit:]
        return [{"role": m.role, "content": m.content} for m in recent]

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "channel": self.channel,
            "user_id": self.user_id,
            "messages": [
                {"role": m.role, "content": m.content, "ts": m.timestamp} for m in self.messages
            ],
            "created_at": self.created_at,
            "last_active": self.last_active,
        }

    @classmethod
    def from_vtx_session(cls, vtx_session: VtxSession, channel: str, user_id: str) -> Session:
        """Build a gateway Session from a vtx Session's active messages."""
        entries: list[SessionEntry] = []
        for entry in vtx_session.active_entries:
            if hasattr(entry, "message") and hasattr(entry.message, "content"):
                text = _message_text(entry.message)
                if text:
                    role = getattr(entry.message, "role", "user")
                    entries.append(
                        SessionEntry(role=role, content=text, timestamp=__import__("time").time())
                    )
        created_ts = __import__("time").time()
        return cls(
            session_id=vtx_session.id,
            channel=channel,
            user_id=user_id,
            messages=entries,
            created_at=created_ts,
            last_active=created_ts,
            _vtx_session=vtx_session,
        )


def _message_text(message: Any) -> str:
    """Extract plain text from a vtx Message object."""
    if isinstance(message.content, str):
        return message.content
    if isinstance(message.content, list):
        parts = [p.text for p in message.content if hasattr(p, "text")]
        return "".join(parts)
    return ""


class SessionManager:
    """Manages channel→session mapping, delegating persistence to vtx sessions.

    Creates one vtx :class:`~vtx.session.Session` per channel/user pair
    and wraps it in the gateway-friendly :class:`Session` dataclass.
    """

    def __init__(self, store_dir: Path | None = None) -> None:
        self._store_dir = store_dir or Path.home() / ".vtx" / "claw" / "sessions"
        self._vtx_sessions_dir: Path | None = None
        self._sessions: dict[str, Session] = {}
        self._source_map: dict[str, str] = {}  # "channel:user_id" -> session_id
        self._store_dir.mkdir(parents=True, exist_ok=True)

    def _cwd_for(self, channel: str, user_id: str) -> str:
        """Get a virtual cwd for the channel/user pair."""
        return os.getcwd()

    def get_or_create(self, channel: str, user_id: str) -> Session:
        key = f"{channel}:{user_id}"
        sid = self._source_map.get(key)
        if sid and sid in self._sessions:
            return self._sessions[sid]

        # Create a vtx session for proper persistence
        cwd = self._cwd_for(channel, user_id)
        vtx_session = VtxSession.create(
            cwd=cwd,
            persist=True,
            provider="",  # set by the agent handler on first use
            model_id="",
            system_prompt="VTX Claw gateway session",
        )

        gw_session = Session(
            session_id=vtx_session.id,
            channel=channel,
            user_id=user_id,
            created_at=__import__("time").time(),
            last_active=__import__("time").time(),
            _vtx_session=vtx_session,
        )
        self._sessions[gw_session.session_id] = gw_session
        self._source_map[key] = gw_session.session_id
        return gw_session

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def list_all(self) -> list[Session]:
        return list(self._sessions.values())

    def save(self, session: Session) -> None:
        """Persist to vtx session if available."""
        if session._vtx_session is not None:
            session._vtx_session.ensure_persisted()

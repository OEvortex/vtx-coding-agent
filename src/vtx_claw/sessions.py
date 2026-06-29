from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class SessionEntry:
    role: str
    content: str
    timestamp: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Session:
    session_id: str
    channel: str
    user_id: str
    messages: list[SessionEntry] = field(default_factory=list)
    created_at: float = 0.0
    last_active: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def add_message(self, role: str, content: str, **metadata: Any) -> SessionEntry:
        entry = SessionEntry(role=role, content=content, timestamp=time.time(), metadata=metadata)
        self.messages.append(entry)
        self.last_active = time.time()
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


class SessionManager:
    def __init__(self, store_dir: Path | None = None) -> None:
        self._store_dir = store_dir or Path.home() / ".vtx" / "claw" / "sessions"
        self._sessions: dict[str, Session] = {}
        self._source_map: dict[str, str] = {}  # "channel:user_id" -> session_id
        self._store_dir.mkdir(parents=True, exist_ok=True)
        self._load_all()

    def _load_all(self) -> None:
        for f in self._store_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text())
                session = Session(
                    session_id=data["session_id"],
                    channel=data["channel"],
                    user_id=data["user_id"],
                    created_at=data.get("created_at", 0),
                    last_active=data.get("last_active", 0),
                    metadata=data.get("metadata", {}),
                )
                for m in data.get("messages", []):
                    session.messages.append(
                        SessionEntry(
                            role=m["role"], content=m["content"], timestamp=m.get("ts", 0)
                        )
                    )
                self._sessions[session.session_id] = session
                key = f"{session.channel}:{session.user_id}"
                self._source_map[key] = session.session_id
            except Exception:
                logger.exception("Failed to load session %s", f.name)

    def get_or_create(self, channel: str, user_id: str) -> Session:
        key = f"{channel}:{user_id}"
        sid = self._source_map.get(key)
        if sid and sid in self._sessions:
            return self._sessions[sid]
        session = Session(
            session_id=str(uuid.uuid4()),
            channel=channel,
            user_id=user_id,
            created_at=time.time(),
            last_active=time.time(),
        )
        self._sessions[session.session_id] = session
        self._source_map[key] = session.session_id
        self._persist(session)
        return session

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def list_all(self) -> list[Session]:
        return list(self._sessions.values())

    def save(self, session: Session) -> None:
        self._persist(session)

    def _persist(self, session: Session) -> None:
        path = self._store_dir / f"{session.session_id}.json"
        path.write_text(json.dumps(session.to_dict(), indent=2))

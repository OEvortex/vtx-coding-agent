"""The swarm board: an append-only feed plus a roster, on disk.

The board is what makes a swarm a swarm rather than N sub-agents: every member
reads one ordered feed, and the sequence number it last saw is its cursor. It
lives in a directory per swarm so it survives the turn, a crash, and the
process — ``board.jsonl`` is one JSON object per post, ``members.json`` is the
roster.

Writes go through :class:`Board` while the engine holds its lock, so appends
are ordered; reads are incremental (we only parse the bytes added since the
last read) because every polling member reads the same file.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

BOARD_FILE = "board.jsonl"
MEMBERS_FILE = "members.json"
CHANNELS_FILE = "channels.json"

#: Every member is always a member of this channel, so it never needs a
#: subscription and cannot be archived.
MAIN = "main"

#: How much of one post the board keeps. A post is a message, not a document.
MAX_POST_CHARS = 16_000

#: Member states, as the tools report them.
WAITING = "waiting"
WORKING = "working"
STOPPED = "stopped"
KILLED = "killed"


@dataclass(frozen=True)
class Post:
    """One thing said on the board."""

    seq: int
    agent: str
    text: str
    time: str
    channel: str = MAIN

    def render(self) -> str:
        where = "" if self.channel == MAIN else f" #{self.channel}"
        return f"#{self.seq}{where} @{self.agent}: {self.text}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "agent": self.agent,
            "text": self.text,
            "time": self.time,
            "channel": self.channel,
        }


@dataclass
class MemberRow:
    """One member as the board records it."""

    name: str
    conversation_id: str = ""
    model: str | None = None
    role: str = "worker"
    state: str = STOPPED
    killed: bool = False
    last_read_seq: int = 0
    wait_mode: str = "mentions"
    note: str = ""
    #: Channels this member follows, besides :data:`MAIN`.
    channels: list[str] = field(default_factory=list)
    #: ``inherit`` | ``share`` | ``branch``.
    workspace: str = "inherit"
    #: Where this member works, when its workspace is its own.
    workspace_dir: str = ""
    #: Roster name that added this member (``coordinator`` for the top level).
    added_by: str = "coordinator"

    def to_dict(self) -> dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "model": self.model,
            "role": self.role,
            "state": self.state,
            "killed": self.killed,
            "last_read_seq": self.last_read_seq,
            "wake_mode": self.wait_mode,
            "note": self.note,
            "channels": list(self.channels),
            "workspace": self.workspace,
            "workspace_dir": self.workspace_dir,
            "added_by": self.added_by,
        }

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> MemberRow:
        return cls(
            name=name,
            conversation_id=str(data.get("conversation_id") or ""),
            model=data.get("model"),
            role=str(data.get("role") or "worker"),
            state=str(data.get("state") or STOPPED),
            killed=bool(data.get("killed")),
            last_read_seq=int(data.get("last_read_seq") or 0),
            wait_mode=str(data.get("wake_mode") or "mentions"),
            note=str(data.get("note") or ""),
            channels=[str(c) for c in (data.get("channels") or [])],
            workspace=str(data.get("workspace") or "inherit"),
            workspace_dir=str(data.get("workspace_dir") or ""),
            added_by=str(data.get("added_by") or "coordinator"),
        )


@dataclass
class Channel:
    """A work area on the board: a topic, its creator, and whether it is done."""

    name: str
    topic: str = ""
    created_by: str = "coordinator"
    created_at: str = ""
    archived: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "archived": self.archived,
        }


@dataclass
class Board:
    """The swarm directory: ``board.jsonl`` + ``members.json``."""

    root: Path
    title: str = ""
    _posts: list[Post] = field(default_factory=list)
    _offset: int = 0
    _rows: dict[str, MemberRow] = field(default_factory=dict)
    _channels: dict[str, Channel] = field(default_factory=dict)
    _loaded: bool = False

    def __post_init__(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    # -- paths ---------------------------------------------------------

    @property
    def board_path(self) -> Path:
        return self.root / BOARD_FILE

    @property
    def members_path(self) -> Path:
        return self.root / MEMBERS_FILE

    @property
    def channels_path(self) -> Path:
        return self.root / CHANNELS_FILE

    # -- feed ----------------------------------------------------------

    @property
    def head(self) -> int:
        """The highest sequence number on the board (0 when empty)."""
        self.refresh()
        return self._posts[-1].seq if self._posts else 0

    @property
    def posts(self) -> list[Post]:
        self.refresh()
        return list(self._posts)

    def refresh(self) -> None:
        """Parse whatever was appended since the last read."""
        path = self.board_path
        try:
            size = path.stat().st_size
        except OSError:
            return
        if size == self._offset:
            return
        try:
            with path.open("rb") as fh:
                fh.seek(self._offset)
                data = fh.read()
        except OSError:
            return
        # Only consume complete lines: a concurrent append may leave a partial
        # one, which the next refresh picks up.
        end = data.rfind(b"\n") + 1
        if not end:
            return
        for raw in data[:end].splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except ValueError:
                continue
            try:
                self._posts.append(
                    Post(
                        seq=int(obj["seq"]),
                        agent=str(obj.get("agent") or "unknown"),
                        text=str(obj.get("text") or ""),
                        time=str(obj.get("time") or ""),
                        channel=str(obj.get("channel") or MAIN),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        self._offset += end

    def append(self, agent: str, text: str, channel: str = MAIN) -> Post:
        """Add a post as ``agent`` and return it."""
        self.refresh()
        seq = (self._posts[-1].seq if self._posts else 0) + 1
        body = " ".join((text or "").split())
        if len(body) > MAX_POST_CHARS:
            body = body[:MAX_POST_CHARS] + " …"
        post = Post(
            seq=seq,
            agent=agent,
            text=body,
            time=time.strftime("%Y-%m-%dT%H:%M:%S"),
            channel=channel or MAIN,
        )
        with self.board_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(post.to_dict(), ensure_ascii=False) + "\n")
        self._posts.append(post)
        self._offset = self.board_path.stat().st_size
        return post

    def since(
        self, seq: int, limit: int | None = None, channels: set[str] | None = None
    ) -> list[Post]:
        """Posts after ``seq``, oldest first, optionally from some channels only."""
        self.refresh()
        out = [
            p for p in self._posts if p.seq > seq and (channels is None or p.channel in channels)
        ]
        if limit is not None and limit > 0 and len(out) > limit:
            out = out[-limit:]
        return out

    def after_tail(self, limit: int = 10) -> list[Post]:
        """The newest ``limit`` posts."""
        self.refresh()
        return self._posts[-limit:] if limit > 0 else []

    # -- roster --------------------------------------------------------

    def load(self) -> None:
        """Read the roster (and title) from disk."""
        if self._loaded:
            return
        self._loaded = True
        try:
            data = json.loads(self.members_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        self.title = str(data.get("title") or "")
        for name, row in (data.get("members") or {}).items():
            if isinstance(row, dict):
                self._rows[name] = MemberRow.from_dict(name, row)

    @property
    def rows(self) -> dict[str, MemberRow]:
        self.load()
        return self._rows

    def save(self) -> None:
        """Write the roster atomically."""
        payload = {
            "title": self.title,
            "members": {name: row.to_dict() for name, row in self.rows.items()},
        }
        self._atomic_write(self.members_path, json.dumps(payload, indent=2, ensure_ascii=False))

    # -- channels ------------------------------------------------------

    def load_channels(self) -> None:
        if self._channels:
            return
        try:
            data = json.loads(self.channels_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for name, spec in (data.get("channels") or {}).items():
            if isinstance(spec, dict):
                self._channels[name] = Channel(
                    name=name,
                    topic=str(spec.get("topic") or ""),
                    created_by=str(spec.get("created_by") or "coordinator"),
                    created_at=str(spec.get("created_at") or ""),
                    archived=bool(spec.get("archived")),
                )

    @property
    def channels(self) -> dict[str, Channel]:
        self.load_channels()
        return self._channels

    def save_channels(self) -> None:
        payload = {"channels": {name: ch.to_dict() for name, ch in self.channels.items()}}
        self._atomic_write(self.channels_path, json.dumps(payload, indent=2, ensure_ascii=False))

    def channel(self, name: str) -> Channel | None:
        clean = (name or "").strip().lower().lstrip("#")
        if clean == MAIN:
            return Channel(name=MAIN, topic="everything", created_by="coordinator")
        return self.channels.get(clean)

    def create_channel(
        self, name: str, topic: str = "", created_by: str = "coordinator"
    ) -> Channel:
        self.load_channels()
        clean = (name or "").strip().lower().lstrip("#")
        channel = Channel(
            name=clean,
            topic=" ".join((topic or "").split())[:120],
            created_by=created_by,
            created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        )
        self._channels[clean] = channel
        self.save_channels()
        return channel

    def upsert(self, row: MemberRow) -> MemberRow:
        self.load()
        self._rows[row.name] = row
        self.save()
        return row

    def rename(self, old: str, new: str) -> None:
        self.load()
        row = self._rows.pop(old, None)
        if row is not None:
            row.name = new
            self._rows[new] = row
            self.save()

    @staticmethod
    def _atomic_write(path: Path, text: str) -> None:
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
            os.replace(tmp, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise


def render_posts(posts: list[Post], *, header: str = "") -> str:
    """The shape a model reads: one line per post, numbered."""
    if not posts:
        return (header + "\n" if header else "") + "(no posts)"
    lines = [header] if header else []
    lines.extend(post.render() for post in posts)
    return "\n".join(lines)


__all__ = [
    "BOARD_FILE",
    "CHANNELS_FILE",
    "KILLED",
    "MAIN",
    "MEMBERS_FILE",
    "STOPPED",
    "WAITING",
    "WORKING",
    "Board",
    "Channel",
    "MemberRow",
    "Post",
    "render_posts",
]

"""5-layer memory for OpenJarvis — local-first, VTX-backed."""

from __future__ import annotations

import contextlib
import shutil as _shutil
import sqlite3
import time
from pathlib import Path
from typing import Any

from vtx.core.paths import get_config_dir


class SkillStore:
    """Procedural skill documents — autonomous SKILL.md creation (Layer 2)."""

    def __init__(self, base: Path | None = None) -> None:
        self.base = base or (get_config_dir() / "openjarvis" / "skills")
        self.base.mkdir(parents=True, exist_ok=True)

    def write_skill(self, name: str, content: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in name)[:64]
        p = self.base / f"{safe}.md"
        p.write_text(content, encoding="utf-8")
        return p

    def list_skills(self) -> list[Path]:
        return sorted(self.base.glob("*.md"))

    def search(self, query: str, limit: int = 5) -> list[Path]:
        q = query.lower()
        scored: list[tuple[int, Path]] = []
        for p in self.list_skills():
            try:
                text = p.read_text(encoding="utf-8").lower()
                score = text.count(q)
                if score:
                    scored.append((score, p))
            except Exception:
                continue
        scored.sort(key=lambda x: x[0], reverse=True)
        return [p for _, p in scored[:limit]]


class FTS5Store:
    """Full-text search over past sessions — SQLite FTS5 (Layer 5)."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or (get_config_dir() / "openjarvis" / "fts5.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as con:
            con.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS sessions_fts "
                "USING fts5(id, content, created_at)"
            )
            con.commit()

    def index(self, session_id: str, content: str) -> None:
        with sqlite3.connect(self.db_path) as con:
            con.execute(
                "INSERT OR REPLACE INTO sessions_fts(id, content, created_at) VALUES (?, ?, ?)",
                (session_id, content, str(int(time.time()))),
            )
            con.commit()

    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        with sqlite3.connect(self.db_path) as con:
            cur = con.execute(
                "SELECT id, snippet(sessions_fts, 1, '<b>', '</b>', '...', 10) "
                "as snippet FROM sessions_fts WHERE sessions_fts MATCH ? LIMIT ?",
                (query, limit),
            )
            rows = cur.fetchall()
            return [{"id": r[0], "snippet": r[1]} for r in rows]


class MemoryStore:
    """Static helpers for the Dream consolidation flow (session bookkeeping)."""

    @staticmethod
    def dream_session_key() -> str:
        """Session key used for ephemeral dream consolidation turns."""
        return "dream:consolidation"

    @staticmethod
    def dream_run_completed(resp: Any) -> bool:
        """Heuristic: a dream run completed when it produced a non-empty reply."""
        content = getattr(resp, "content", resp) if resp is not None else None
        return bool(content and str(content).strip())

    @staticmethod
    def build_dream_commit_message(label: str, resp: Any) -> str:
        """Compose a git commit message for a dream consolidation run."""
        return f"{label} ({MemoryStore.dream_session_key()})"

    @staticmethod
    def prune_dream_sessions(sessions_dir: Path, *, keep: int = 10) -> int:
        """Delete old ephemeral dream sessions; returns the number removed."""
        removed = 0
        try:
            dream_dirs = sorted(
                (p for p in Path(sessions_dir).glob("*dream*") if p.is_dir()),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for stale in dream_dirs[keep:]:
                _shutil.rmtree(stale, ignore_errors=True)
                removed += 1
        except Exception:
            pass
        return removed


class OpenJarvisMemory:
    """Facade for all 5 memory layers."""

    def __init__(self) -> None:
        self.skills = SkillStore()
        self.fts5 = FTS5Store()
        # Layers 1 (context window) is managed by VTX compaction.
        # Layers 3 (vector) and 4 (Honcho) are stubbed as optional.
        self.vector_enabled = False
        self.honcho_enabled = False

    def remember_session(self, session_id: str, summary: str) -> None:
        with contextlib.suppress(Exception):
            self.fts5.index(session_id, summary)

    def create_skill_from_task(self, task: str, solution: str) -> Path | None:
        content = (
            f"# Skill: {task}\n\n## Solution\n\n{solution}\n\n## Created\n\n{int(time.time())}\n"
        )
        try:
            return self.skills.write_skill(task, content)
        except Exception:
            return None

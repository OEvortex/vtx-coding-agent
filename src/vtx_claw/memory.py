from __future__ import annotations

import json
import logging
import time
from datetime import date
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class MemoryManager:
    def __init__(self, store_dir: Path | None = None, daily_logs: bool = True) -> None:
        self._store_dir = store_dir or Path.home() / ".vtx" / "claw" / "memory"
        self._store_dir.mkdir(parents=True, exist_ok=True)
        self._daily_logs = daily_logs
        self._json_path = self._store_dir / "memories.json"
        self._entries: dict[str, list[dict[str, Any]]] = {}
        self._load_all()

    def _load_all(self) -> None:
        if self._json_path.exists():
            try:
                data = json.loads(self._json_path.read_text())
                raw = data.get("entries", {})
                self._entries = {k: v for k, v in raw.items() if isinstance(v, list)}
            except Exception:
                logger.exception("Failed to load memory store")

    def remember(self, user_id: str, key: str, value: str) -> None:
        entry = {
            "key": key,
            "value": value,
            "ts": time.time(),
            "date": str(date.today()),
            "user_id": user_id,
        }
        self._entries.setdefault(user_id, []).append(entry)
        if self._daily_logs:
            self._append_daily_log(user_id, entry)
        self._persist()

    def recall(self, user_id: str, query: str = "") -> list[dict[str, Any]]:
        entries = list(reversed(self._entries.get(user_id, [])[-50:]))
        if not query:
            return entries
        q = query.lower()
        return [
            e for e in entries if q in e.get("key", "").lower() or q in e.get("value", "").lower()
        ]

    def get_all(self, user_id: str) -> list[dict[str, Any]]:
        return list(reversed(self._entries.get(user_id, [])[-50:]))

    def delete(self, user_id: str, key: str) -> bool:
        entries = self._entries.get(user_id, [])
        before = len(entries)
        self._entries[user_id] = [e for e in entries if e.get("key") != key]
        if len(self._entries[user_id]) < before:
            self._persist()
            return True
        return False

    def format_for_prompt(self, user_id: str) -> str:
        entries = self.recall(user_id)
        if not entries:
            return ""
        lines = [f"- {e['key']}: {e['value']}" for e in entries[-40:]]
        return "User memories:\n" + "\n".join(lines)

    def load_tools_md(self) -> str:
        p = Path.home() / ".vtx" / "claw" / "TOOLS.md"
        if p.exists():
            return p.read_text()
        return ""

    def _persist(self) -> None:
        self._json_path.write_text(json.dumps({"entries": self._entries}, indent=2))

    def _append_daily_log(self, user_id: str, entry: dict[str, Any]) -> None:
        today = date.today().isoformat()
        log = self._store_dir / f"{today}.md"
        line = f"- [{entry['ts']:.0f}] [{user_id}] **{entry['key']}**: {entry['value']}\n"
        try:
            with log.open("a") as f:
                f.write(line)
        except Exception:
            logger.exception("Failed to append daily log")

"""Prompt history that outlives a session.

fx keeps what you typed between runs, so the up arrow at a fresh prompt
recalls yesterday's prompts. vx kept its history in memory, which meant a new
window started blank. This stores it under the ctx config dir, one JSON string
per line, and keeps the file small by capping it.
"""

from __future__ import annotations

import json
from pathlib import Path

#: How many prompts to keep, newest last.
MAX_ENTRIES = 500


def history_path() -> Path:
    """Where vx keeps its prompt history."""
    try:
        from ctx.core.paths import get_config_dir

        return get_config_dir() / "vx-prompt-history.jsonl"
    except Exception:  # pragma: no cover - config dir is always there in practice
        return Path.home() / ".config" / "ctx" / "vx-prompt-history.jsonl"


def load(limit: int = MAX_ENTRIES) -> list[str]:
    """The prompts kept from earlier runs, oldest first."""
    path = history_path()
    if not path.exists():
        return []
    entries: list[str] = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except Exception:
                value = line
            if isinstance(value, str) and value.strip():
                entries.append(value)
    except OSError:
        return []
    return entries[-limit:]


def save(entries: list[str], limit: int = MAX_ENTRIES) -> None:
    """Rewrite the history file, keeping the newest ``limit`` prompts."""
    path = history_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        with temp.open("w", encoding="utf-8") as fh:
            for entry in entries[-limit:]:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        temp.replace(path)
    except OSError:
        return


def append(entry: str, existing: list[str], limit: int = MAX_ENTRIES) -> list[str]:
    """``existing`` with ``entry`` added once at the end, and saved."""
    text = entry.strip()
    if not text:
        return existing
    # Repeating the last prompt should not fill the file with duplicates, but
    # an older prompt asked again is worth keeping where it was asked.
    if existing and existing[-1] == text:
        return existing
    entries = [*existing, text][-limit:]
    save(entries, limit)
    return entries

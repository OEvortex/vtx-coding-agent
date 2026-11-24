"""Prompt history, shared with ctx's own TUI.

vx began with a private file, which meant the prompts typed in ctx and the
prompts typed in vx were two separate memories of the same person. ctx already
keeps ``prompt-history.jsonl`` under its config dir, one JSON string per line,
so vx reads and writes that file: an up arrow recalls what you asked in either
front end, in the order you asked it.

The cap matches ctx's for the same reason -- if vx wrote more entries than ctx
keeps, ctx would trim the file on its next append and the two would fight.
"""

from __future__ import annotations

import json
from pathlib import Path

#: How many prompts to keep, newest last. ctx's own limit, so the file agrees.
MAX_ENTRIES = 50


def history_path() -> Path:
    """The history file ctx's TUI also uses."""
    try:
        from ctx.core.paths import get_config_dir

        return get_config_dir() / "prompt-history.jsonl"
    except Exception:  # pragma: no cover - the config dir is there in practice
        return Path.home() / ".config" / "ctx" / "prompt-history.jsonl"


def _legacy_path() -> Path:
    return history_path().with_name("vx-prompt-history.jsonl")


def _read(path: Path) -> list[str]:
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
    return entries


def _migrate_once() -> None:
    """Fold in the file vx wrote before it shared ctx's.

    Without this, the prompts from earlier vx sessions would fall off the
    history the first time the shared file wins.
    """
    legacy = _legacy_path()
    if not legacy.exists():
        return
    old = _read(legacy)
    if old:
        # The legacy file is the newer one -- it was written last by vx -- so
        # its prompts go after ctx's and survive the cap.
        current = _read(history_path())
        merged = [*current, *(entry for entry in old if entry not in current)]
        save(merged)
    try:
        legacy.rename(legacy.with_suffix(".migrated"))
    except OSError:
        return


def load(limit: int = MAX_ENTRIES) -> list[str]:
    """The prompts kept from earlier runs, oldest first."""
    _migrate_once()
    entries = _read(history_path())
    if len(entries) > limit:
        entries = entries[-limit:]
        save(entries, limit)
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
    entries = [*existing, text]
    if len(entries) > limit:
        entries = entries[-limit:]
        save(entries, limit)
        return entries
    # Under the cap the file only grows, which is what ctx appends too.
    path = history_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(text, ensure_ascii=False) + "\n")
    except OSError:
        return existing
    return entries

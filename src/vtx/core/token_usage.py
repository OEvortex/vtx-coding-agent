"""Durable lifetime token ledger.

Session logs used to be the *only* record of how many tokens Vtx had spent, so
deleting an old session silently rewrote history: ``/usage``'s lifetime total
was really "total across the sessions that still exist". Retention (old,
untouched sessions are pruned at startup) makes that untenable, so usage is now
also accumulated into ``token_usage.json`` beside the sessions directory.

The ledger is a cache with a memory: every session file it has ever seen keeps
its per-day buckets even after the file is deleted. A file whose ``(mtime,
size)`` still matches is not re-parsed, so the ledger also makes ``/usage``
cheap -- the first run pays for the scan, every later run reads one small JSON
document plus whatever changed.

Session logs are append-only, so a matching signature means the recorded
buckets are still exact. A resumed session that rewrites its file changes both
mtime and size and gets re-parsed. Sub-agent sessions are separate files and
therefore count once, on their own; the parent transcript's ``subagent_usage``
entries are deliberately *not* read here, or the lifetime total would double.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

#: Written immediately next to the ``sessions/`` tree it summarizes.
LEDGER_FILENAME = "token_usage.json"
LEDGER_VERSION = 1

#: Index layout of a day bucket; shared with ``usage_stats``.
INPUT = 0
OUTPUT = 1
CACHE_READ = 2
CACHE_WRITE = 3
TURNS = 4
BUCKET_WIDTH = 5

_LOCK = threading.RLock()


@dataclass
class LedgerFile:
    """One session file's contribution, durable beyond the file itself."""

    mtime: float
    size: int
    days: dict[str, list[int]] = field(default_factory=dict)


def default_ledger_path(sessions_root: Path) -> Path:
    """``<config dir>/token_usage.json`` given ``<config dir>/sessions``."""
    return sessions_root.parent / LEDGER_FILENAME


def _load(ledger_path: Path) -> dict[str, LedgerFile]:
    try:
        raw = json.loads(ledger_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    files = raw.get("files")
    if not isinstance(files, dict):
        return {}
    parsed: dict[str, LedgerFile] = {}
    for key, value in files.items():
        if not isinstance(value, dict):
            continue
        days_raw = value.get("days")
        if not isinstance(days_raw, dict):
            continue
        days: dict[str, list[int]] = {}
        for day, bucket in days_raw.items():
            if not isinstance(day, str) or not isinstance(bucket, list):
                continue
            numbers = []
            for item in bucket[:BUCKET_WIDTH]:
                try:
                    numbers.append(int(item))
                except (TypeError, ValueError):
                    numbers.append(0)
            numbers.extend([0] * (BUCKET_WIDTH - len(numbers)))
            days[day] = numbers
        try:
            mtime = float(value.get("mtime") or 0.0)
            size = int(value.get("size") or 0)
        except (TypeError, ValueError):
            mtime, size = 0.0, 0
        parsed[str(key)] = LedgerFile(mtime=mtime, size=size, days=days)
    return parsed


def _save(ledger_path: Path, files: dict[str, LedgerFile]) -> bool:
    """Atomically rewrite the ledger. Returns False when it could not be written."""
    payload = {
        "version": LEDGER_VERSION,
        "files": {
            key: {"mtime": entry.mtime, "size": entry.size, "days": entry.days}
            for key, entry in sorted(files.items())
        },
    }
    try:
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=f"{ledger_path.name}.", suffix=".tmp", dir=ledger_path.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, separators=(",", ":"))
            os.replace(tmp_name, ledger_path)
        except Exception:
            with contextlib.suppress(OSError):
                Path(tmp_name).unlink()
            raise
    except OSError:
        return False
    return True


def _key(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _entry_date(entry: Any) -> date | None:
    """The calendar day an entry was recorded on.

    Prefers the entry's own timestamp and falls back to the session filename,
    which is prefixed with the creation date -- so a session whose entries
    lack timestamps still lands on the right day instead of being dropped.
    """
    if isinstance(entry, dict):
        raw = entry.get("timestamp")
        if isinstance(raw, str):
            try:
                # Local, not UTC: "yesterday's usage" is a question about the
                # user's clock. Logs are stored in UTC, so a session that runs
                # at 00:30 IST would otherwise be filed under the day before.
                return datetime.fromisoformat(raw).astimezone().date()
            except ValueError:
                pass
    return None


def session_date(path: Path) -> date | None:
    try:
        return datetime.fromisoformat(path.stem[:10]).date()
    except ValueError:
        return None


def scan_session_file(path: Path, fallback: date | None = None) -> dict[str, list[int]]:
    """Parse one session log into ``{iso_date: bucket}``."""
    raw: dict[str, list[int]] = {}
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                # Skip anything that cannot carry usage: on real data that is
                # most lines, which is what keeps the full scan affordable.
                if '"usage"' not in line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(entry, dict):
                    continue
                message = entry.get("message")
                if not isinstance(message, dict) or message.get("role") != "assistant":
                    continue
                usage = message.get("usage")
                if not isinstance(usage, dict):
                    continue
                day = _entry_date(entry) or fallback
                if day is None:
                    continue
                bucket = raw.setdefault(day.isoformat(), [0] * BUCKET_WIDTH)
                bucket[INPUT] += int(usage.get("input_tokens") or 0)
                bucket[OUTPUT] += int(usage.get("output_tokens") or 0)
                bucket[CACHE_READ] += int(usage.get("cache_read_tokens") or 0)
                bucket[CACHE_WRITE] += int(usage.get("cache_write_tokens") or 0)
                bucket[TURNS] += 1
    except OSError:
        pass
    return raw


def sync_usage_ledger(
    session_paths: list[Path],
    *,
    root: Path,
    ledger_path: Path | None = None,
    require_persist: bool = False,
) -> dict[str, list[int]]:
    """Fold ``session_paths`` into the ledger and return all recorded days.

    Files that are gone from disk stay in the returned totals -- that is the
    whole point of the ledger -- so the result is lifetime usage, not usage of
    the sessions that happen to still exist.

    ``require_persist`` turns a failed write into an ``OSError``. Retention
    passes it: deleting a file whose usage never reached disk would lose those
    tokens for good, so the caller must be able to stop instead.

    Blocking; call from a worker thread.
    """
    ledger = ledger_path or default_ledger_path(root)
    with _LOCK:
        files = _load(ledger)
        changed = False
        for path in session_paths:
            try:
                stat = path.stat()
            except OSError:
                continue
            key = _key(path, root)
            known = files.get(key)
            if known is not None and known.mtime == stat.st_mtime and known.size == stat.st_size:
                continue
            files[key] = LedgerFile(
                mtime=stat.st_mtime,
                size=stat.st_size,
                days=scan_session_file(path, session_date(path)),
            )
            changed = True
        if changed and not _save(ledger, files) and require_persist:
            raise OSError(f"could not write usage ledger: {ledger}")

        merged: dict[str, list[int]] = {}
        for entry in files.values():
            for day, bucket in entry.days.items():
                target = merged.setdefault(day, [0] * BUCKET_WIDTH)
                for index, value in enumerate(bucket):
                    target[index] += value
        return merged

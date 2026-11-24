"""Durable lifetime token ledger.

Session logs used to be the *only* record of how many tokens Ctx had spent, so
deleting an old session silently rewrote history: ``/usage``'s lifetime total
was really "total across the sessions that still exist". Retention (old,
untouched sessions are pruned on startup) makes that untenable, so usage is
also accumulated into the database, keyed by session file.

The ledger is a cache with a memory: every session file it has ever seen keeps
its per-day buckets even after the file is deleted. A file whose ``(mtime,
size)`` still matches is not re-parsed, so the ledger also makes ``/usage``
cheap -- the first run pays for the scan, every later run is one grouped SQL
aggregate plus whatever changed.

Session logs are append-only, so a matching signature means the recorded
buckets are still exact. A resumed session that rewrites its file changes both
mtime and size and gets re-parsed. Sub-agent sessions are separate files and
therefore count once, on their own; the parent transcript's ``subagent_usage``
entries are deliberately *not* read here, or the lifetime total would double.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from ctx.core import store

#: Index layout of a day bucket; shared with ``usage_stats``.
INPUT = 0
OUTPUT = 1
CACHE_READ = 2
CACHE_WRITE = 3
TURNS = 4
BUCKET_WIDTH = 5


def _key(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _entry_date(entry: object) -> date | None:
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
    session_paths: list[Path], *, root: Path, require_persist: bool = False
) -> dict[str, list[int]]:
    """Fold ``session_paths`` into the ledger and return all recorded days.

    Files that are gone from disk stay in the returned totals -- that is the
    whole point of the ledger -- so the result is lifetime usage, not usage of
    the sessions that happen to still exist.

    ``require_persist`` turns a failed write into an ``OSError``. Retention
    passes it: deleting a file whose usage never reached the database would
    lose those tokens for good, so the caller must be able to stop instead.

    Blocking; call from a worker thread.
    """
    signatures = store.usage_signatures()
    pending: list[tuple[str, float, int, dict[str, list[int]]]] = []
    for path in session_paths:
        try:
            stat = path.stat()
        except OSError:
            continue
        key = _key(path, root)
        known = signatures.get(key)
        if known is not None and known[0] == stat.st_mtime and known[1] == stat.st_size:
            continue
        pending.append(
            (key, stat.st_mtime, stat.st_size, scan_session_file(path, session_date(path)))
        )

    if pending:
        try:
            with store.write() as conn:
                for key, mtime, size, days in pending:
                    store.put_usage_file(key, mtime, size, days, conn)
        except Exception as exc:
            if require_persist:
                raise OSError(f"could not write usage ledger: {exc}") from exc

    return store.usage_days()

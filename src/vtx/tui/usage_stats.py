"""Token usage aggregated across session logs, for ``/usage``.

Session logs are the only durable record of usage: each assistant message
carries the provider's reported counts, and nothing else accumulates them.
So the history is reconstructed by reading them rather than kept in a
side-ledger that could drift from the logs it summarizes.

That is affordable because the scan skips any line without ``"usage"``, which
on real data is most of them: a 751-session / 654 MB history parses in about
three seconds. Caching that was tried and dropped -- the active session's mtime
changes on every turn, so any fingerprint taken over the whole set misses every
time and rewrites the cache for nothing. Callers must still run this off-thread
(see ``_show_usage``), because three seconds on the event loop is a freeze.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from vtx.core.paths import get_config_dir

#: A year gives the activity grid enough history to show long term patterns.
HEATMAP_DAYS = 365


@dataclass
class DailyUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    turns: int = 0

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
        )


@dataclass
class UsageReport:
    #: ISO date -> usage, for the heatmap window only.
    days: dict[date, DailyUsage] = field(default_factory=dict)
    #: Same shape across every session ever written.
    lifetime: DailyUsage = field(default_factory=DailyUsage)
    last_30_days: DailyUsage = field(default_factory=DailyUsage)
    sessions_scanned: int = 0


def _sessions_root() -> Path:
    return get_config_dir() / "sessions"


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


def _session_date(path: Path) -> date | None:
    try:
        return datetime.fromisoformat(path.stem[:10]).date()
    except ValueError:
        return None


def _accumulate(entry: dict[str, Any], fallback: date | None) -> tuple[date, DailyUsage] | None:
    message = entry.get("message")
    if not isinstance(message, dict) or message.get("role") != "assistant":
        return None
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return None
    day = _entry_date(entry) or fallback
    if day is None:
        return None
    record = DailyUsage(
        input_tokens=int(usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
        cache_read_tokens=int(usage.get("cache_read_tokens") or 0),
        cache_write_tokens=int(usage.get("cache_write_tokens") or 0),
        turns=1,
    )
    return day, record


def _add(into: DailyUsage, other: DailyUsage) -> None:
    into.input_tokens += other.input_tokens
    into.output_tokens += other.output_tokens
    into.cache_read_tokens += other.cache_read_tokens
    into.cache_write_tokens += other.cache_write_tokens
    into.turns += other.turns


def _scan(paths: list[Path]) -> dict[str, list[int]]:
    """Return ``{iso_date: [input, output, cache_read, cache_write, turns]}``."""
    raw: dict[str, list[int]] = {}
    for path in paths:
        fallback = _session_date(path)
        try:
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    if '"usage"' not in line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(entry, dict):
                        continue
                    found = _accumulate(entry, fallback)
                    if found is None:
                        continue
                    day, record = found
                    bucket = raw.setdefault(day.isoformat(), [0, 0, 0, 0, 0])
                    bucket[0] += record.input_tokens
                    bucket[1] += record.output_tokens
                    bucket[2] += record.cache_read_tokens
                    bucket[3] += record.cache_write_tokens
                    bucket[4] += record.turns
        except OSError:
            continue
    return raw


def _to_daily(bucket: list[int]) -> DailyUsage:
    return DailyUsage(
        input_tokens=bucket[0],
        output_tokens=bucket[1],
        cache_read_tokens=bucket[2],
        cache_write_tokens=bucket[3],
        turns=bucket[4],
    )


def aggregate_usage(days: int = HEATMAP_DAYS, *, today: date | None = None) -> UsageReport:
    """Usage for the last ``days``, the last 30 days, and all time.

    Blocking: ~1s for 30 days and ~3s lifetime on a large history. Call from a
    worker thread.
    """
    now = today or date.today()
    root = _sessions_root()
    paths = sorted(root.glob("*/*.jsonl")) if root.exists() else []
    raw = _scan(paths)

    parsed = {date.fromisoformat(k): _to_daily(v) for k, v in raw.items()}

    window_start = now - timedelta(days=days - 1)
    report = UsageReport(sessions_scanned=len(paths))
    thirty_ago = now - timedelta(days=29)
    for day, usage in parsed.items():
        _add(report.lifetime, usage)
        # The 30-day total is its own window, not derived from the grid: a
        # shorter grid must not shrink the number reported beside it.
        if thirty_ago <= day <= now:
            _add(report.last_30_days, usage)
        if window_start <= day <= now:
            report.days[day] = usage
    return report

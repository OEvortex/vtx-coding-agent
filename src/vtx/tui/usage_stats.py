"""Token usage aggregated across session logs, for ``/usage``.

Every assistant message carries the provider's reported counts, so the session
logs are the source of truth for usage. Reading them on each request was
affordable only because the scan skips lines without ``"usage"`` -- but it also
meant the lifetime total shrank whenever a session file was deleted, which
retention now does on purpose.

So the scan feeds a durable ledger (:mod:`vtx.core.token_usage`) that remembers
a session file's per-day buckets after the file is gone. ``/usage`` reads the
ledger, and only parses files whose ``(mtime, size)`` changed since the last
sync -- the first call on a large history still parses everything (seconds),
later ones are a small JSON read. Callers must still run this off-thread (see
``_show_usage``), because that first parse on the event loop is a freeze.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from vtx.core.paths import get_config_dir
from vtx.core.token_usage import default_ledger_path, sync_usage_ledger

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
    #: Same shape across every session ever recorded, deleted ones included.
    lifetime: DailyUsage = field(default_factory=DailyUsage)
    last_30_days: DailyUsage = field(default_factory=DailyUsage)
    sessions_scanned: int = 0
    #: The day the windows were computed for. The heatmap must draw this date,
    #: not ``date.today()``, or a report built for a fixed date renders its
    #: grid over the wrong weeks.
    as_of: date | None = None


def _sessions_root() -> Path:
    return get_config_dir() / "sessions"


def _ledger_path() -> Path:
    """The ledger sits beside the sessions tree it summarizes.

    Deriving it from ``_sessions_root()`` rather than the config dir directly
    keeps the two together: anything that redirects the sessions directory
    (tests, an alternate root) redirects the ledger with it.
    """
    return default_ledger_path(_sessions_root())


def _add(into: DailyUsage, other: DailyUsage) -> None:
    into.input_tokens += other.input_tokens
    into.output_tokens += other.output_tokens
    into.cache_read_tokens += other.cache_read_tokens
    into.cache_write_tokens += other.cache_write_tokens
    into.turns += other.turns


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

    Blocking: ~1s for 30 days and ~3s lifetime on a large history the first
    time; near-instant afterwards because unchanged logs are not re-parsed.
    Call from a worker thread.
    """
    now = today or date.today()
    root = _sessions_root()
    paths = sorted(root.glob("*/*.jsonl")) if root.exists() else []
    raw = sync_usage_ledger(paths, root=root, ledger_path=_ledger_path())

    parsed = {date.fromisoformat(k): _to_daily(v) for k, v in raw.items()}

    window_start = now - timedelta(days=days - 1)
    report = UsageReport(sessions_scanned=len(paths), as_of=now)
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

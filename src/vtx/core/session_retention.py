"""Retention for old, unused session logs.

Sessions accumulate forever otherwise: every conversation in every project is
one append-only JSONL file, and nothing ever removed one. The rule here is
deliberately conservative -- a file is only deleted when it is both *old*
(untouched for ``max_age_days``) and *surplus* (not among the ``keep_recent``
newest files in its project directory).

Deleting the transcript does not delete the money. Token usage is folded into
the durable ledger (:mod:`vtx.core.token_usage`) *before* anything is unlinked,
so ``/usage`` keeps reporting lifetime totals after the files are gone.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from vtx.core.bytes_util import format_bytes
from vtx.core.paths import get_config_dir
from vtx.core.token_usage import default_ledger_path, sync_usage_ledger

#: Untouched for this long before a session is eligible, whatever the count.
DEFAULT_MAX_AGE_DAYS = 90
#: The newest N sessions per project directory are always kept.
DEFAULT_KEEP_RECENT = 20


@dataclass
class RetentionPolicy:
    enabled: bool = True
    max_age_days: int = DEFAULT_MAX_AGE_DAYS
    keep_recent: int = DEFAULT_KEEP_RECENT


@dataclass
class PruneResult:
    deleted: list[Path] = field(default_factory=list)
    freed_bytes: int = 0
    kept: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self, policy: RetentionPolicy) -> str:
        files = len(self.deleted)
        plural = "" if files == 1 else "s"
        detail = format_bytes(self.freed_bytes)
        if policy.max_age_days:
            detail += f", older than {policy.max_age_days} days"
        return f"Removed {files} unused session file{plural} ({detail}); token usage is kept."


def policy_from_config() -> RetentionPolicy:
    from vtx.core.config import get_config

    section = get_config().session_retention
    return RetentionPolicy(
        enabled=section.enabled, max_age_days=section.max_age_days, keep_recent=section.keep_recent
    )


def sessions_root() -> Path:
    return get_config_dir() / "sessions"


def prune_old_sessions(
    *,
    policy: RetentionPolicy | None = None,
    root: Path | None = None,
    protect: Iterable[Path] = (),
    now: datetime | None = None,
) -> PruneResult:
    """Delete session files that are old and not among the newest per directory.

    ``protect`` names files that must survive regardless of age -- the session
    this process is currently using. Blocking; call from a worker thread.
    """
    policy = policy or policy_from_config()
    if not policy.enabled:
        return PruneResult()

    root = root or sessions_root()
    if not root.is_dir():
        return PruneResult()

    moment = now or datetime.now(UTC)
    cutoff = moment - timedelta(days=policy.max_age_days) if policy.max_age_days else None
    protected = {path.resolve() for path in protect}

    candidates: list[Path] = []
    kept = 0
    buckets: list[Path] = []
    for bucket in sorted(root.iterdir()):
        if not bucket.is_dir():
            continue
        buckets.append(bucket)
        entries: list[tuple[float, Path]] = []
        for path in bucket.glob("*.jsonl"):
            try:
                entries.append((path.stat().st_mtime, path))
            except OSError:
                continue
        entries.sort(key=lambda item: item[0], reverse=True)
        for index, (mtime, path) in enumerate(entries):
            if index < policy.keep_recent:
                kept += 1
                continue
            if cutoff is not None and datetime.fromtimestamp(mtime, tz=UTC) >= cutoff:
                kept += 1
                continue
            if path.resolve() in protected:
                kept += 1
                continue
            candidates.append(path)

    result = PruneResult(kept=kept)
    if not candidates:
        return result

    # Record usage first. A file deleted before its tokens reached the ledger
    # would vanish from the lifetime total, which is the one thing pruning is
    # not allowed to do.
    all_paths = sorted(root.glob("*/*.jsonl"))
    try:
        sync_usage_ledger(all_paths, root=root, ledger_path=default_ledger_path(root))
    except Exception as exc:
        result.errors.append(f"usage ledger: {exc}")
        return result

    for path in candidates:
        try:
            size = path.stat().st_size
            path.unlink()
        except FileNotFoundError:
            continue
        except OSError as exc:
            result.errors.append(f"{path.name}: {exc}")
            continue
        result.deleted.append(path)
        result.freed_bytes += size

    # A project directory that no longer holds any session is dead weight too.
    for bucket in buckets:
        try:
            if not any(bucket.iterdir()):
                bucket.rmdir()
        except OSError:
            pass

    return result


def prune_in_background(*, protect: Iterable[Path] = ()) -> threading.Thread:
    """Run :func:`prune_old_sessions` off the caller's thread.

    Errors are swallowed: retention is housekeeping, and losing it must never
    fail a session. Returns the thread so a caller can join it in tests.
    """

    def _run() -> None:
        with contextlib.suppress(Exception):
            prune_old_sessions(protect=protect)

    thread = threading.Thread(target=_run, name="vtx-session-retention", daemon=True)
    thread.start()
    return thread

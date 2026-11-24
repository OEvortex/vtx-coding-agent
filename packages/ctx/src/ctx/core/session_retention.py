"""Retention for old, unused sessions.

Sessions accumulate forever otherwise: every conversation in every project is
a set of rows in ``ctx.db``, and nothing ever removed one. The rule here is
deliberately conservative -- a session is only deleted when it is both *old*
(untouched for ``max_age_days``) and *surplus* (not among the ``keep_recent``
newest in its project folder).

Deleting the transcript does not delete the money. Token usage is folded into
the durable ledger (:mod:`ctx.core.store`) as the rows go, so ``/usage`` keeps
reporting lifetime totals after the session is gone.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ctx.core import store
from ctx.core.bytes_util import format_bytes
from ctx.core.paths import get_config_dir

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
    from ctx.core.config import get_config

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
    """Delete sessions that are old and not among the newest per project folder.

    ``protect`` names sessions that must survive regardless of age -- the one
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
    protected = {str(path) for path in protect}

    candidates: list[tuple[Path, int]] = []
    kept = 0

    for bucket in sorted(root.iterdir()):
        if not bucket.is_dir():
            continue
        from ctx.agent.session import import_legacy_sessions

        with contextlib.suppress(Exception):
            import_legacy_sessions(bucket)
        rows = store.list_sessions(bucket.name)
        conn = store.read()
        for index, row in enumerate(rows):  # already newest first
            path = Path(row["path"])
            if index < policy.keep_recent:
                kept += 1
                continue
            try:
                modified = datetime.fromisoformat(row["modified"])
            except (TypeError, ValueError):
                kept += 1
                continue
            if cutoff is not None and modified >= cutoff:
                kept += 1
                continue
            if str(path) in protected:
                kept += 1
                continue
            size_row = conn.execute(
                "SELECT COALESCE(SUM(length(payload)), 0) AS n FROM entry WHERE session_path = ?",
                (str(path),),
            ).fetchone()
            candidates.append((path, int(size_row["n"]) if size_row else 0))

    result = PruneResult(kept=kept)
    for path, size in candidates:
        try:
            # Folds the tokens into the ledger and drops the rows together.
            store.delete_session(str(path))
        except Exception as exc:
            result.errors.append(f"{path.name}: {exc}")
            continue
        result.deleted.append(path)
        result.freed_bytes += size

    return result


def prune_in_background(*, protect: Iterable[Path] = ()) -> threading.Thread:
    """Run :func:`prune_old_sessions` off the caller's thread.

    Errors are swallowed: retention is housekeeping, and losing it must never
    fail a session. Returns the thread so a caller can join it in tests.
    """

    def _run() -> None:
        with contextlib.suppress(Exception):
            prune_old_sessions(protect=protect)

    thread = threading.Thread(target=_run, name="ctx-session-retention", daemon=True)
    thread.start()
    return thread

"""Token usage outlives the session logs it came from.

Retention deletes old session files, so a ledger that merely mirrored them
would make ``/usage``'s lifetime total drop every time housekeeping ran. These
tests pin the two properties the feature rests on: deleting a session keeps its
tokens, and the ledger is cheap to re-read because unchanged logs are skipped.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from vtx.core.session_retention import PruneResult, RetentionPolicy, prune_old_sessions
from vtx.tui import usage_stats

TODAY = date(2026, 10, 4)


def _assistant_usage(day: date, *, inp: int, out: int, cached: int = 0) -> str:
    stamp = datetime.combine(day, datetime.min.time()).astimezone(UTC).isoformat()
    return json.dumps(
        {
            "type": "message",
            "timestamp": stamp,
            "message": {
                "role": "assistant",
                "usage": {
                    "input_tokens": inp,
                    "output_tokens": out,
                    "cache_read_tokens": cached,
                    "cache_write_tokens": 0,
                },
            },
        }
    )


def _subagent_usage(*, inp: int) -> str:
    """Parent-transcript charge for a sub-agent that has its own session file."""
    return json.dumps(
        {
            "type": "custom_message",
            "custom_type": "subagent_usage",
            "timestamp": datetime.combine(TODAY, datetime.min.time()).astimezone(UTC).isoformat(),
            "content": "sub-agent",
            "display": False,
            "details": {"input_tokens": inp, "output_tokens": 0},
        }
    )


@pytest.fixture
def history(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "sessions"
    bucket = root / "-tmp-project"
    bucket.mkdir(parents=True)
    monkeypatch.setattr(usage_stats, "_sessions_root", lambda: root)
    return bucket


def _write(bucket: Path, name: str, lines: list[str], *, age_days: int = 0) -> Path:
    path = bucket / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if age_days:
        stamp = (datetime.now(UTC) - timedelta(days=age_days)).timestamp()
        os.utime(path, (stamp, stamp))
    return path


def test_usage_survives_session_deletion(history: Path) -> None:
    path = _write(
        history, "2026-10-04T09-00-00_a.jsonl", [_assistant_usage(TODAY, inp=1000, out=50)]
    )

    before = usage_stats.aggregate_usage(today=TODAY)
    assert before.lifetime.total_tokens == 1050
    assert before.sessions_scanned == 1
    # The ledger is a sibling of the sessions tree, not a file inside it.
    assert (history.parent.parent / "token_usage.json").exists()

    path.unlink()
    after = usage_stats.aggregate_usage(today=TODAY)

    assert after.sessions_scanned == 0
    # The whole point: the transcript is gone, the cost is not.
    assert after.lifetime.total_tokens == 1050
    assert after.days[TODAY].turns == 1


def test_unchanged_session_is_not_reparsed(history: Path, monkeypatch) -> None:
    _write(history, "2026-10-04T09-00-00_a.jsonl", [_assistant_usage(TODAY, inp=1000, out=0)])
    usage_stats.aggregate_usage(today=TODAY)

    scans = 0
    real = usage_stats.sync_usage_ledger

    def counting(*args, **kwargs):
        nonlocal scans
        scans += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(usage_stats, "sync_usage_ledger", counting)
    # The ledger, not the session log, is what makes the second call cheap.
    from vtx.core import token_usage

    scanned: list[Path] = []
    monkeypatch.setattr(
        token_usage, "scan_session_file", lambda p, f=None: scanned.append(p) or {}
    )

    report = usage_stats.aggregate_usage(today=TODAY)
    assert scans == 1
    assert scanned == []
    assert report.lifetime.total_tokens == 1000


def test_appended_session_is_rerun_through_the_ledger(history: Path) -> None:
    path = _write(
        history, "2026-10-04T09-00-00_a.jsonl", [_assistant_usage(TODAY, inp=1000, out=0)]
    )
    assert usage_stats.aggregate_usage(today=TODAY).lifetime.total_tokens == 1000

    with path.open("a", encoding="utf-8") as handle:
        handle.write(_assistant_usage(TODAY, inp=500, out=0) + "\n")

    assert usage_stats.aggregate_usage(today=TODAY).lifetime.total_tokens == 1500


def test_subagent_charges_are_not_double_counted(history: Path) -> None:
    """The sub-agent's own log carries the usage; the parent only references it."""
    _write(
        history,
        "2026-10-04T09-00-00_parent.jsonl",
        [_assistant_usage(TODAY, inp=100, out=0), _subagent_usage(inp=900)],
    )
    _write(history, "2026-10-04T09-00-00_child.jsonl", [_assistant_usage(TODAY, inp=900, out=0)])

    report = usage_stats.aggregate_usage(today=TODAY)
    assert report.lifetime.input_tokens == 1000


def test_corrupt_ledger_is_rebuilt_from_logs(history: Path) -> None:
    _write(history, "2026-10-04T09-00-00_a.jsonl", [_assistant_usage(TODAY, inp=700, out=0)])
    (history.parent.parent / "token_usage.json").write_text("{not json", encoding="utf-8")

    report = usage_stats.aggregate_usage(today=TODAY)
    assert report.lifetime.input_tokens == 700


def test_pruning_keeps_usage_in_the_lifetime_total(history: Path) -> None:
    old = _write(
        history,
        "2026-01-01T09-00-00_old.jsonl",
        [_assistant_usage(date(2026, 1, 1), inp=4000, out=0)],
        age_days=200,
    )
    recent = _write(
        history, "2026-10-04T09-00-00_new.jsonl", [_assistant_usage(TODAY, inp=100, out=0)]
    )

    result = prune_old_sessions(
        policy=RetentionPolicy(enabled=True, max_age_days=90, keep_recent=1), root=history.parent
    )

    assert result.deleted == [old]
    assert not old.exists()
    assert recent.exists()
    assert isinstance(result, PruneResult)
    assert "token usage is kept" in result.summary(RetentionPolicy())

    report = usage_stats.aggregate_usage(today=TODAY)
    assert report.sessions_scanned == 1
    assert report.lifetime.input_tokens == 4100


def test_pruning_respects_keep_recent_and_protect(history: Path) -> None:
    paths = [
        _write(
            history,
            f"2026-01-0{i}T09-00-00_s{i}.jsonl",
            [_assistant_usage(date(2026, 1, i), inp=10, out=0)],
            age_days=200 + i,
        )
        for i in range(1, 5)
    ]

    result = prune_old_sessions(
        policy=RetentionPolicy(enabled=True, max_age_days=90, keep_recent=1),
        root=history.parent,
        protect=[paths[3]],
    )

    # The newest file is kept by count; the oldest is kept by name.
    assert sorted(result.deleted) == sorted([paths[1], paths[2]])
    assert paths[0].exists()
    assert paths[3].exists()


def test_young_sessions_are_never_pruned(history: Path) -> None:
    kept = [_write(history, f"2026-10-0{i}T09-00-00_a.jsonl", ["{}"]) for i in range(1, 4)]

    result = prune_old_sessions(
        policy=RetentionPolicy(enabled=True, max_age_days=90, keep_recent=0), root=history.parent
    )

    assert result.deleted == []
    assert all(path.exists() for path in kept)


def test_disabled_retention_deletes_nothing(history: Path) -> None:
    old = _write(history, "2026-01-01T09-00-00_old.jsonl", ["{}"], age_days=500)

    result = prune_old_sessions(
        policy=RetentionPolicy(enabled=False, max_age_days=1, keep_recent=0), root=history.parent
    )

    assert result.deleted == []
    assert old.exists()


def test_pruning_stops_when_the_ledger_cannot_be_written(history: Path, monkeypatch) -> None:
    """Losing the ledger must lose nothing else: no write, no delete."""
    from vtx.core import token_usage

    old = _write(
        history,
        "2026-01-01T09-00-00_old.jsonl",
        [_assistant_usage(date(2026, 1, 1), inp=4000, out=0)],
        age_days=200,
    )
    monkeypatch.setattr(token_usage, "_save", lambda *args, **kwargs: False)

    result = prune_old_sessions(
        policy=RetentionPolicy(enabled=True, max_age_days=90, keep_recent=0), root=history.parent
    )

    assert result.deleted == []
    assert result.errors
    assert old.exists()
    assert usage_stats.aggregate_usage(today=TODAY).lifetime.input_tokens == 4000


def test_empty_project_directory_is_removed_with_its_last_session(history: Path) -> None:
    _write(history, "2026-01-01T09-00-00_old.jsonl", ["{}"], age_days=500)

    prune_old_sessions(
        policy=RetentionPolicy(enabled=True, max_age_days=90, keep_recent=0), root=history.parent
    )

    assert not history.exists()

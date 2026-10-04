"""``/usage`` -- session, 30-day and lifetime totals, plus the heatmap.

Two things had to be right for this to be worth having: the numbers must come
from the same source of truth as the session log (no drifting side-ledger), and
the grid must line up, because a misaligned contribution graph looks exactly
like a rendering bug and reads as one.
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from vtx.tui.app import Vtx
from vtx.tui.blocks import UsageBlock
from vtx.tui.usage_stats import DailyUsage, aggregate_usage

ANSI = re.compile(r"\x1b\[[0-9;]*m")

TODAY = date(2026, 10, 4)

#: (days before ``TODAY``, input tokens) -- offset 2 is deliberately zero so a
#: day with usage but no volume is exercised.
USAGE_DAYS = (
    (0, 1000),
    (1, 250_000),
    (2, 0),
    (10, 40_000),
    *((offset, 1000 * offset) for offset in range(3, 10)),
)


def _usage_entry(day: date, *, inp: int, out: int, cached: int = 0) -> str:
    ts = datetime.combine(day, datetime.min.time(), tzinfo=UTC).isoformat()
    return json.dumps(
        {
            "type": "message",
            "timestamp": ts,
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


@pytest.fixture
def fake_history(tmp_path, monkeypatch):
    """Point the aggregator at a throwaway session directory."""
    from vtx.tui import usage_stats

    sessions = tmp_path / "sessions" / "home-test"
    sessions.mkdir(parents=True)

    monkeypatch.setattr(usage_stats, "_sessions_root", lambda: tmp_path / "sessions")
    today = date(2026, 10, 4)
    for offset, tokens in (
        (0, 1000),
        (1, 250_000),
        (2, 0),
        (10, 40_000),
        *((o, 1000 * o) for o in range(3, 10)),
    ):
        day = today - timedelta(days=offset)
        name = f"{day.isoformat()}T09-00-00_test.jsonl"
        body = "\n".join(
            (
                json.dumps({"type": "session_start", "timestamp": day.isoformat()}),
                _usage_entry(day, inp=tokens, out=tokens // 10, cached=tokens // 4),
                # A non-usage line, which the scanner must skip without caring.
                json.dumps({"type": "note", "timestamp": day.isoformat()}),
            )
        )
        (sessions / name).write_text(body + "\n", encoding="utf-8")
    return tmp_path


def _expected_total(tokens: int) -> int:
    return tokens + tokens // 10 + tokens // 4


def test_aggregate_reads_every_window(fake_history) -> None:
    report = aggregate_usage(days=30, today=TODAY)

    assert report.sessions_scanned == len(USAGE_DAYS)
    expected = sum(_expected_total(tokens) for _, tokens in USAGE_DAYS)
    assert report.lifetime.total_tokens == expected
    # Every fixture day is inside 30 days of TODAY, so the rolling window and
    # the lifetime are the same set here.
    assert report.last_30_days.total_tokens == expected
    assert set(report.days) == {TODAY - timedelta(days=o) for o, _ in USAGE_DAYS}
    # A day that had a turn but no volume is present with zero totals, rather
    # than missing from the map -- otherwise "idle" and "never happened" look
    # identical on the grid.
    zero_day = report.days[TODAY - timedelta(days=2)]
    assert zero_day.turns == 1
    assert zero_day.total_tokens == 0


def test_aggregate_excludes_days_outside_the_window(fake_history) -> None:
    report = aggregate_usage(days=5, today=TODAY)

    assert TODAY - timedelta(days=10) not in report.days
    # The grid narrows but the 30-day and lifetime totals do not: they are
    # their own windows, not derived from `days`.
    assert report.last_30_days.total_tokens == report.lifetime.total_tokens
    assert report.days[TODAY].total_tokens == _expected_total(1000)


def test_heatmap_labels_and_grid_shape(fake_history) -> None:
    report = aggregate_usage(days=30, today=TODAY)
    block = UsageBlock(report, DailyUsage(turns=1), 30)
    plain = ANSI.sub("", block._build().plain)

    assert "Sep" in plain and "Oct" in plain
    # Weekday gutter: rows are Mon/Wed/Fri, aligned with a Sunday-start grid.
    gutter = [line[:4] for line in plain.splitlines() if line.startswith(("  m ", "  w ", "  f "))]
    assert len(gutter) == 3
    # Every grid line -- the month header included -- is the same width, or
    # the columns do not line up. Trailing blanks are real columns, so this
    # must not rstrip.
    grid = [
        line
        for line in plain.splitlines()
        if ("░" in line or "▒" in line or "▓" in line or "█" in line) and "less" not in line
    ]
    widths = {len(line) for line in grid}
    assert len(widths) == 1, f"ragged grid rows: {widths}"
    header = next(line for line in plain.splitlines() if "Sep" in line and "Oct" in line)
    assert len(header) == len(grid[0]), "month labels do not line up with their columns"


def test_heatmap_pads_a_full_seven_day_window(fake_history) -> None:
    """A window that does not start on a Sunday is padded, not ragged."""
    report = aggregate_usage(days=30, today=TODAY)
    plain = ANSI.sub("", UsageBlock(report, DailyUsage(), 30)._build().plain)
    rows = [
        line
        for line in plain.splitlines()
        if "░" in line or "▒" in line or "▓" in line or "█" in line
    ]
    rows = [r for r in rows if "less" not in r]
    assert len(rows) == 7, f"expected 7 weekday rows, got {len(rows)}"


def test_empty_history_still_renders(fake_history, monkeypatch) -> None:
    """No sessions at all must produce a panel, not an exception."""
    import shutil

    shutil.rmtree(fake_history / "sessions")
    report = aggregate_usage(days=30, today=TODAY)
    plain = ANSI.sub("", UsageBlock(report, DailyUsage(), 30)._build().plain)

    assert report.sessions_scanned == 0
    assert "Token usage" in plain
    assert "Lifetime" in plain


@pytest.mark.asyncio
async def test_usage_command_mounts_the_panel(tmp_path) -> None:
    app = Vtx(cwd=str(tmp_path))
    async with app.run_test(size=(100, 40)) as pilot:
        app.run_worker(app.action_usage(), exclusive=True)
        for _ in range(100):
            await pilot.pause()
            if app.query(UsageBlock):
                break
        blocks = app.query(UsageBlock)
        assert blocks, "/usage mounted no UsageBlock"

        plain = ANSI.sub("", blocks[0]._build().plain)
        assert "Token usage" in plain
        assert "Session" in plain
        assert "Last 30 days" in plain
        assert "Lifetime" in plain


def test_daily_usage_total_is_the_sum_of_its_parts() -> None:
    usage = DailyUsage(
        input_tokens=1_000_000,
        output_tokens=20_000,
        cache_read_tokens=900_000,
        cache_write_tokens=5_000,
        turns=7,
    )
    assert usage.total_tokens == 1_925_000
    assert usage.turns == 7


def test_token_format_uses_1024_units() -> None:
    from vtx.tui.formatting import format_tokens

    assert format_tokens(1023) == "1023"
    assert format_tokens(1024) == "1k"
    assert format_tokens(1536) == "1.5k"
    assert format_tokens(1 << 20) == "1M"
    assert format_tokens(3 * (1 << 30)) == "3B"
    assert format_tokens(2 * (1 << 40)) == "2T"


@pytest.mark.asyncio
async def test_session_turns_come_from_message_counts(monkeypatch) -> None:
    """Regression: the panel showed ``0 turns`` for a live session.

    ``token_totals()`` carries no turn count, so it must come from
    ``message_counts()``; leaving it unset silently rendered zero.
    """
    from vtx.tui.commands.sessions import SessionCommands

    session = SimpleNamespace(
        token_totals=lambda: SimpleNamespace(
            input_tokens=100, output_tokens=20, cache_read_tokens=5, cache_write_tokens=0
        ),
        message_counts=lambda: SimpleNamespace(assistant_messages=3),
    )
    chat = SimpleNamespace(
        add_info_message=lambda *a, **k: SimpleNamespace(remove=lambda: None),
        mount=lambda block: mounted.append(block),
    )
    mounted: list[UsageBlock] = []
    commands = SimpleNamespace(
        _runtime=SimpleNamespace(session=session), query_one=lambda *a, **k: chat
    )
    monkeypatch.setattr("vtx.tui.usage_stats._sessions_root", lambda: Path(os.devnull))

    await SessionCommands.action_usage(commands)  # type: ignore[arg-type]

    assert mounted and "3 turns" in ANSI.sub("", mounted[0]._build().plain)

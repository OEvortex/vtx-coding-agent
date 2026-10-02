"""The InfoBar context gauge and its auto-compaction threshold tick."""

from __future__ import annotations

import pytest

from vtx.core.config import config
from vtx.core.harness_config import get_harness_config
from vtx.tui.widgets import CONTEXT_METER_WIDTH, InfoBar


@pytest.fixture
def threshold(request):
    """Set the compaction threshold for one test, then restore the real one."""

    def _set(percent: float) -> None:
        harness = get_harness_config()
        original = harness.compaction_threshold_percent
        harness.compaction_threshold_percent = percent
        request.addfinalizer(lambda: setattr(harness, "compaction_threshold_percent", original))

    return _set


def _meter(used: int, window: int = 200_000, percent: float = 80.0) -> str:
    bar = InfoBar(".", "model", context_window=window)
    bar.set_tokens(0, 0, context_tokens=used)
    return bar._format_context_meter().plain


def test_meter_is_absent_without_a_context_window() -> None:
    assert _meter(0, window=0) == ""


def test_meter_width_is_fixed() -> None:
    # Two end caps plus CONTEXT_METER_WIDTH cells.
    assert len(_meter(0)) == CONTEXT_METER_WIDTH + 2


def test_meter_is_empty_at_zero() -> None:
    assert set(_meter(0).strip("▕▏")) == {"░", "┊"}


def test_meter_fills_proportionally() -> None:
    assert _meter(100_000).count("█") == CONTEXT_METER_WIDTH // 2


def test_meter_never_overflows() -> None:
    assert "█" * (CONTEXT_METER_WIDTH + 1) not in _meter(999_999)


def test_threshold_tick_stays_visible_after_fill_passes_it() -> None:
    assert "┊" in _meter(190_000)


def test_threshold_tick_moves_with_the_configured_threshold(threshold) -> None:
    threshold(25.0)
    early = _meter(0)
    threshold(90.0)
    late = _meter(0)
    assert early.index("┊") < late.index("┊")


def test_meter_turns_to_notice_colour_past_the_threshold(threshold) -> None:
    threshold(80.0)
    colors = config.ui.colors
    under = InfoBar(".", "model", context_window=200_000)
    under.set_tokens(0, 0, context_tokens=40_000)
    over = InfoBar(".", "model", context_window=200_000)
    over.set_tokens(0, 0, context_tokens=190_000)

    assert colors.accent in {str(s.style) for s in under._format_context_meter().spans}
    assert colors.notice in {str(s.style) for s in over._format_context_meter().spans}


def test_meter_is_included_in_row1_right() -> None:
    bar = InfoBar(".", "model", context_window=200_000)
    bar.set_tokens(0, 0, context_tokens=100_000)
    row = bar._format_row1_right().plain
    assert "▕" in row
    assert "100k/200k" in row

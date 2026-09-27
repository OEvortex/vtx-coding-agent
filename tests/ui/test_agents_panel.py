"""Tests for the pinned sub-agent panel (vtx.tui.agents_panel)."""

import pytest
from textual.app import App, ComposeResult

from vtx.tui import task_ui
from vtx.tui.agents_panel import (
    AgentsPanel,
    activity_for,
    metrics_for,
    render_agents,
    should_show,
    visible_runs,
)
from vtx.tui.goal_agents import SubagentRegistry
from vtx.tui.widgets import InfoBar


def _registry_with(*, running: int = 0, queued: int = 0, finished: int = 0) -> SubagentRegistry:
    registry = SubagentRegistry()
    for i in range(running):
        registry.record(
            f"r{i}", {"kind": "subagent_start", "subagent": "Explore", "description": f"Task {i}"}
        )
    for i in range(queued):
        registry.record(
            f"q{i}", {"kind": "subagent_queued", "subagent": "Explore", "position": i + 1}
        )
    for i in range(finished):
        registry.record(
            f"f{i}", {"kind": "subagent_start", "subagent": "Explore", "description": f"Done {i}"}
        )
        registry.record("f" + str(i), {"kind": "subagent_end", "subagent": "Explore", "turns": 1})
    return registry


def test_panel_draws_one_row_per_running_subagent() -> None:
    registry = _registry_with(running=4)
    text = render_agents(registry.runs(), width=100, frame=2).plain
    lines = text.splitlines()

    assert lines[0].strip() == "● Agents"
    # One line per running agent: the activity shares the row, so nothing is
    # stacked underneath.
    assert sum(1 for line in lines if "─ " in line) == 4
    assert sum(1 for line in lines if "thinking…" in line) == 4


def test_queued_subagents_collapse_to_one_summary_line() -> None:
    registry = _registry_with(running=2, queued=28)
    text = render_agents(registry.runs(), width=100).plain
    assert text.rstrip().endswith("│ ○ 28 queued")
    # Queued agents get no rows of their own.
    assert text.count("queued ·") == 0
    assert "ahead of the cap" not in text


def test_same_named_subagents_each_get_a_row() -> None:
    registry = _registry_with(running=4)
    rows = [
        line
        for line in render_agents(registry.runs(), width=100).plain.splitlines()
        if "─ " in line
    ]
    assert len(rows) == 4
    assert all("Explore" in row for row in rows)


def test_row_shows_counters_and_activity() -> None:
    registry = SubagentRegistry()
    registry.record(
        "a",
        {
            "kind": "subagent_start",
            "subagent": "Plan",
            "label": "Map the refactor",
            "description": "Plan agent (read-only)",
        },
    )
    registry.record("a", {"kind": "tool_start", "subagent": "Plan", "tool_name": "read"})
    registry.record("a", {"kind": "turn_end", "subagent": "Plan", "turns": 1, "tokens": 8700})
    registry.record(
        "a", {"kind": "text_delta", "subagent": "Plan", "delta": "pi-chonky-subagents is a [pi…"}
    )
    registry.record("a", {"kind": "tool_result", "subagent": "Plan", "tool_name": "read"})

    text = render_agents(registry.runs(), width=100).plain
    # One line carries the task, the agent, the activity and the counters.
    row = next(line for line in text.splitlines() if "Map the refactor" in line)
    assert "Plan" in row
    # The task leads; the agent's profile blurb is secondary detail.
    assert "Plan agent (read-only)" not in text
    assert "1 tool · 8.7k tokens" in row
    # No tool in flight, so the row shows what the agent last streamed.
    assert "pi-chonky-subagents is a [pi…" in row


def test_thinking_tool_and_response_share_one_row() -> None:
    """The row is the live view: thinking, tool call and response in place."""
    registry = SubagentRegistry()
    for index, name in enumerate(("alpha", "beta", "gamma")):
        registry.record(f"r{index}", {"kind": "subagent_start", "subagent": name, "label": name})
    # alpha is mid tool, beta is thinking, gamma is streaming its answer.
    registry.record("r0", {"kind": "tool_start", "subagent": "alpha", "tool_name": "grep"})
    registry.record(
        "r2", {"kind": "text_delta", "subagent": "gamma", "delta": "the parser is in turn.py"}
    )

    lines = render_agents(registry.runs(), width=110).plain.splitlines()
    rows = [line for line in lines if "─ " in line]
    assert len(rows) == 3, "each agent is exactly one line"

    alpha, beta, gamma = rows
    assert "grep · searching…" in alpha
    assert "thinking…" in beta
    assert "the parser is in turn.py" in gamma
    # No agent needed a second line for any of it.
    assert all(line.endswith(("0.0s", "0.1s", "0.2s")) or "tokens" in line for line in rows)


def test_streaming_response_keeps_the_newest_words_visible() -> None:
    """Left-truncated, so the text that just arrived is what stays on screen."""
    registry = SubagentRegistry()
    registry.record("a", {"kind": "subagent_start", "subagent": "x", "label": "Long answer"})
    registry.record(
        "a",
        {
            "kind": "text_delta",
            "subagent": "x",
            "delta": "I checked every module under src/vtx/ai and src/vtx/tui first.",
        },
    )
    row = next(
        line
        for line in render_agents(registry.runs(), width=120).plain.splitlines()
        if "─ " in line
    )
    # The oldest words fall off the left; the words that just arrived stay,
    # so the row ends where the sub-agent currently is.
    tail = row.split("…")[-1].split("  ")[0].strip()
    assert "…odule" in row, "the tail is truncated from the left, not the right"
    assert tail.endswith("src/vtx/tui first.")
    assert "I checked" not in row


def test_counters_stay_in_one_column() -> None:
    """Fixed columns are what make the counters scannable.

    The activity is padded to a common width, so the counters begin at the
    same offset on every row however long the activity was.
    """
    registry = SubagentRegistry()
    for index, label in enumerate(("short", "a much longer task label", "mid")):
        registry.record(f"r{index}", {"kind": "subagent_start", "subagent": "Explore"})
        registry.record(f"r{index}", {"kind": "text_delta", "subagent": "Explore", "delta": label})
    rows = [
        line
        for line in render_agents(registry.runs(), width=100).plain.splitlines()
        if "─ " in line
    ]
    assert len(rows) == 3
    ends = {len(line.rstrip()) for line in rows}
    assert len(ends) == 1, f"counter block is ragged: {sorted(ends)}"


def test_finished_run_collapses_into_a_summary_line() -> None:
    registry = SubagentRegistry()
    registry.record("a", {"kind": "subagent_start", "subagent": "Plan", "label": "Find the bug"})
    registry.record(
        "a", {"kind": "text_delta", "subagent": "Plan", "delta": "found the auth bug in login()"}
    )
    registry.record("a", {"kind": "subagent_end", "subagent": "Plan", "turns": 2, "tokens": 900})
    run = registry.runs()[0]
    # The run keeps its last words for anyone who asks...
    assert activity_for(run) == "found the auth bug in login()"
    # ...but the strip does not spend a two-line row on it.
    text = render_agents([run], width=100).plain
    assert text == "● Agents\n│ ✓ 1 finished · 900 tokens"


def test_failed_runs_are_summarised_with_a_failure_count() -> None:
    """A dead run gets no row of its own; the summary has to say it failed."""
    registry = SubagentRegistry()
    registry.record("a", {"kind": "subagent_start", "subagent": "Plan", "label": "Plan it"})
    registry.record("a", {"kind": "error", "subagent": "Plan", "error": "rate limited"})
    registry.record("a", {"kind": "subagent_end", "subagent": "Plan"})
    registry.record("b", {"kind": "subagent_start", "subagent": "Plan", "label": "Plan it again"})
    registry.record("b", {"kind": "subagent_end", "subagent": "Plan", "turns": 2, "tokens": 900})

    text = render_agents(registry.runs(), width=100).plain
    summary = text.rsplit("\n", 1)[-1]
    assert summary.startswith("│ ✗")
    assert "2 finished" in summary
    assert "1 failed" in summary
    # The error text stays reachable on the run itself.
    assert activity_for(registry.runs()[0]) == "rate limited"


def test_rows_are_capped_and_the_rest_collapse() -> None:
    registry = _registry_with(running=10)
    text = render_agents(registry.runs(), width=100, max_rows=3).plain
    assert "… +7 more running" in text
    # Three real rows (each with a spinner) plus the collapse line.
    spinners = sum(1 for line in text.splitlines() if any(f in line for f in task_ui.SPINNER))
    assert spinners == 3


def test_last_visible_row_closes_the_tree() -> None:
    registry = _registry_with(running=2)
    text = render_agents(registry.runs(), width=100).plain
    assert "└─" in text


def test_metrics_are_blank_for_queued_runs() -> None:
    registry = _registry_with(queued=1)
    run = registry.runs()[0]
    assert metrics_for(run) == ""
    assert activity_for(run).startswith("queued ·")


def test_panel_hides_when_nothing_is_in_flight() -> None:
    import time

    registry = _registry_with(finished=1)
    run = registry.runs()[0]
    # Freshly finished: the row lingers long enough to be read.
    assert should_show([run], now=time.monotonic()) is True
    # Long finished: the strip gives its space back.
    assert should_show([run], now=time.monotonic() + 60) is False
    assert should_show([], now=time.monotonic()) is False


def test_panel_shows_while_a_subagent_is_queued_or_running() -> None:
    assert should_show(_registry_with(running=1).runs()) is True
    assert should_show(_registry_with(queued=3).runs()) is True


def test_pruning_drops_old_finished_runs_but_never_live_ones() -> None:
    """Turn-boundary housekeeping must not blink out a background sub-agent."""
    from vtx.tui.goal_agents import REGISTRY

    REGISTRY.clear()
    try:
        REGISTRY.record("live", {"kind": "subagent_start", "subagent": "Explore"})
        REGISTRY.record("queued", {"kind": "subagent_queued", "subagent": "Explore"})
        REGISTRY.record("old", {"kind": "subagent_start", "subagent": "Explore"})
        REGISTRY.record("old", {"kind": "subagent_end", "subagent": "Explore"})

        dropped = REGISTRY.prune_finished(0.0)
        assert dropped == 1
        assert {run.run_id for run in REGISTRY.runs()} == {"live", "queued"}
    finally:
        REGISTRY.clear()


def test_turn_boundary_keeps_a_background_subagent_alive() -> None:
    from vtx.tui.goal_agents import REGISTRY, prune_finished_subagents

    REGISTRY.clear()
    try:
        # A background dispatch from the previous turn, still running.
        REGISTRY.record("bg", {"kind": "subagent_start", "subagent": "Explore"})
        REGISTRY.record("old", {"kind": "subagent_start", "subagent": "Explore"})
        REGISTRY.record("old", {"kind": "subagent_end", "subagent": "Explore"})

        prune_finished_subagents(0.0)
        assert [run.run_id for run in REGISTRY.runs()] == ["bg"]
    finally:
        REGISTRY.clear()


def test_only_running_agents_get_rows() -> None:
    """Queued ones ride the count line, finished ones the summary line."""
    registry = _registry_with(running=1, queued=2, finished=1)
    rows = visible_runs(registry.runs())
    assert len(rows) == 1
    assert rows[0].running is True


def test_narrow_terminal_truncates_without_overflowing() -> None:
    registry = _registry_with(running=3)
    text = render_agents(registry.runs(), width=40).plain
    for line in text.splitlines():
        assert len(line) <= 40


# ---------------------------------------------------------------------------
# Mounted behaviour
# ---------------------------------------------------------------------------


class PanelApp(App[None]):
    """Just the panel, mounted the way the real app mounts it."""

    def compose(self) -> ComposeResult:
        yield AgentsPanel(id="agents-panel")


@pytest.mark.asyncio
async def test_panel_hides_until_a_subagent_is_in_flight() -> None:
    from vtx.tui.goal_agents import REGISTRY

    REGISTRY.clear()
    try:
        async with PanelApp().run_test() as pilot:
            panel = pilot.app.query_one(AgentsPanel)
            assert not panel.has_class("-visible")

            REGISTRY.record("a", {"kind": "subagent_start", "subagent": "Explore"})
            panel.refresh_panel()
            await pilot.pause()
            assert panel.has_class("-visible")
            assert panel.counts == (1, 0)

            REGISTRY.record("q", {"kind": "subagent_queued", "subagent": "Explore"})
            panel.refresh_panel()
            await pilot.pause()
            assert panel.counts == (1, 1)
    finally:
        REGISTRY.clear()


def test_info_bar_shows_the_running_queued_split() -> None:
    bar = InfoBar(".", "model")
    assert "running" not in bar._format_row2_left().plain

    bar.set_subagents(4, 28)
    plain = bar._format_row2_left().plain
    assert "4 running, 28 queued agents" in plain

    bar.set_subagents(1, 0)
    assert "1 running agent" in bar._format_row2_left().plain

    bar.set_subagents(0, 0)
    assert "running" not in bar._format_row2_left().plain

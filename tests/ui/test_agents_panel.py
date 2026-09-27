"""Tests for the pinned sub-agent panel (vtx.tui.agents_panel)."""

import pytest
from textual.app import App, ComposeResult

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
    assert sum(1 for line in lines if "─ " in line) == 4
    # Every running row has a live sub-line under it.
    assert sum(1 for line in lines if line.strip().endswith("thinking…")) == 4


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
        {"kind": "subagent_start", "subagent": "Plan", "description": "Plan agent (read-only)"},
    )
    registry.record("a", {"kind": "tool_start", "subagent": "Plan", "tool_name": "read"})
    registry.record("a", {"kind": "turn_end", "subagent": "Plan", "turns": 1, "tokens": 8700})
    registry.record(
        "a", {"kind": "text_delta", "subagent": "Plan", "delta": "pi-chonky-subagents is a [pi…"}
    )
    registry.record("a", {"kind": "tool_result", "subagent": "Plan", "tool_name": "read"})

    text = render_agents(registry.runs(), width=100).plain
    assert "Plan agent (read-only)" in text
    assert "1 tool use · 8.7k tokens" in text
    # No tool in flight, so the sub-line shows what the agent last said.
    assert "pi-chonky-subagents is a [pi…" in text


def test_finished_run_shows_its_last_words() -> None:
    registry = SubagentRegistry()
    registry.record("a", {"kind": "subagent_start", "subagent": "Plan"})
    registry.record(
        "a", {"kind": "text_delta", "subagent": "Plan", "delta": "found the auth bug in login()"}
    )
    registry.record("a", {"kind": "subagent_end", "subagent": "Plan", "turns": 2, "tokens": 900})
    run = registry.runs()[0]
    assert activity_for(run) == "found the auth bug in login()"
    assert "✓" in render_agents([run], width=100).plain


def test_error_run_shows_the_error() -> None:
    registry = SubagentRegistry()
    registry.record("a", {"kind": "subagent_start", "subagent": "Plan"})
    registry.record("a", {"kind": "error", "subagent": "Plan", "error": "rate limited"})
    registry.record("a", {"kind": "subagent_end", "subagent": "Plan"})
    text = render_agents(registry.runs(), width=100).plain
    assert "✗" in text
    assert "rate limited" in text


def test_rows_are_capped_and_the_rest_collapse() -> None:
    registry = _registry_with(running=10)
    text = render_agents(registry.runs(), width=100, max_rows=3).plain
    assert "… +7 more agents" in text
    assert sum(1 for line in text.splitlines() if "─ " in line) == 3


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


def test_visible_runs_puts_running_first_and_omits_queued() -> None:
    registry = _registry_with(running=1, queued=2, finished=1)
    names = [run.name for run in visible_runs(registry.runs())]
    assert len(names) == 2
    assert all(run.queued is False for run in visible_runs(registry.runs()))


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

"""The pinned Agents panel inside the real app layout.

The panel is only useful if it stays put: mounted under the status line, above
the input box, it has to survive the chat scrolling behind it and take its
width from the terminal rather than from a hardcoded layout.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from vtx.coding_agent.tui import agents_panel
from vtx.coding_agent.tui.agents_panel import AgentsPanel
from vtx.coding_agent.tui.app import Vtx
from vtx.coding_agent.tui.goal_agents import REGISTRY
from vtx.coding_agent.tui.widgets import InfoBar


@pytest.fixture
def clean_registry():
    REGISTRY.clear()
    yield
    REGISTRY.clear()


def _start(app: Vtx, call_id: str, description: str) -> None:
    """Dispatch a sub-agent the way the Task tool does."""
    app._task_progress_callback(
        call_id, {"kind": "subagent_start", "subagent": "Explore", "description": description}
    )


@pytest.mark.asyncio
async def test_panel_is_hidden_with_no_subagents(tmp_path, clean_registry) -> None:
    app = Vtx(cwd=str(tmp_path))
    async with app.run_test(size=(100, 30)):
        panel = app.query_one("#agents-panel", AgentsPanel)
        assert not panel.has_class("-visible")
        assert panel.region.height == 0


@pytest.mark.asyncio
async def test_panel_shows_a_row_per_subagent_and_sits_above_the_input(
    tmp_path, clean_registry
) -> None:
    app = Vtx(cwd=str(tmp_path))
    async with app.run_test(size=(100, 30)) as pilot:
        panel = app.query_one("#agents-panel", AgentsPanel)
        input_box = app.query_one("#input-box")

        _start(app, "t1", "Find TODO/FIXME comments")
        _start(app, "t2", "Count files and LOC")
        # Two dispatches of the same agent name must not collapse into one row.
        app._task_progress_callback("t1", {"kind": "tool_start", "tool_name": "grep"})
        app._task_progress_callback("t2", {"kind": "tool_start", "tool_name": "read"})
        app._task_progress_callback("q1", {"kind": "subagent_queued", "subagent": "Explore"})
        await pilot.pause()

        assert panel.has_class("-visible")
        rendered = str(panel.content)
        rows = [line for line in rendered.splitlines() if "─ " in line]
        assert len(rows) == 2, f"one line per sub-agent, got {rows}"
        # Each row carries its own task and its own live tool.
        assert any("Find TODO/FIXME comments" in row and "grep · sear" in row for row in rows)
        assert any("Count files and LOC" in row and "read · read" in row for row in rows)
        assert "○ 1 queued" in rendered

        # Pinned: the strip sits between the status line and the editor.
        status = app.query_one("#status-line")
        assert status.region.y < panel.region.y < input_box.region.y
        assert panel.region.width <= input_box.region.width


@pytest.mark.asyncio
async def test_panel_counts_reach_the_info_bar(tmp_path, clean_registry) -> None:
    app = Vtx(cwd=str(tmp_path))
    async with app.run_test(size=(100, 30)) as pilot:
        footer = app.query_one("#compact-footer", InfoBar)

        _start(app, "t1", "Map public API surface")
        _start(app, "t2", "Analyze git history")
        app._task_progress_callback("q1", {"kind": "subagent_queued", "subagent": "Explore"})
        app._task_progress_callback("q2", {"kind": "subagent_queued", "subagent": "Explore"})
        await pilot.pause()

        assert footer._subagents_running == 2
        assert footer._subagents_queued == 2

        app._task_progress_callback(
            "t1", {"kind": "subagent_end", "subagent": "Explore", "turns": 2, "tokens": 900}
        )
        app._task_progress_callback(
            "t2", {"kind": "subagent_end", "subagent": "Explore", "turns": 1, "tokens": 400}
        )
        await pilot.pause()
        assert (footer._subagents_running, footer._subagents_queued) == (0, 2)


@pytest.mark.asyncio
async def test_panel_retires_once_everything_lands(tmp_path, clean_registry) -> None:
    app = Vtx(cwd=str(tmp_path))
    async with app.run_test(size=(100, 30)) as pilot:
        panel = app.query_one("#agents-panel", AgentsPanel)

        _start(app, "t1", "Map public API surface")
        await pilot.pause()
        assert panel.has_class("-visible")

        app._task_progress_callback(
            "t1", {"kind": "subagent_end", "subagent": "Explore", "turns": 1, "tokens": 100}
        )
        await pilot.pause()
        # The finished row lingers briefly so it can be read...
        assert panel.has_class("-visible")

        # ...then the strip gives its space back.
        from vtx.coding_agent.tui.goal_agents import DONE_LINGER_SECONDS

        run = REGISTRY.runs()[0]
        run.ended_at = time.monotonic() - DONE_LINGER_SECONDS - 1
        panel.refresh_panel()
        await pilot.pause()
        assert not panel.has_class("-visible")
        assert panel.region.height == 0


@pytest.mark.asyncio
async def test_panel_unpins_itself_after_the_linger_window(
    tmp_path, clean_registry, monkeypatch
) -> None:
    """The regression: a finished row used to stay pinned forever.

    The animation timer stopped as soon as nothing was running, so the panel
    never re-checked whether its linger window had closed — the last row sat
    there for the rest of the session.
    """
    monkeypatch.setattr(agents_panel, "DONE_LINGER_SECONDS", 0.2)

    app = Vtx(cwd=str(tmp_path))
    async with app.run_test(size=(100, 30)) as pilot:
        panel = app.query_one("#agents-panel", AgentsPanel)

        _start(app, "t1", "Rate the VTX codebase")
        await pilot.pause()
        assert panel.has_class("-visible")

        app._task_progress_callback(
            "t1", {"kind": "subagent_end", "subagent": "Explore", "turns": 9, "tokens": 859_200}
        )
        await pilot.pause()
        assert panel.has_class("-visible")

        # No further events: only the panel's own clock can unpin it.
        await asyncio.sleep(1.0)
        await pilot.pause()
        assert not panel.has_class("-visible")
        assert panel.region.height == 0

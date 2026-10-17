"""Widget-level tests for the pinned Todos strip.

The rendering is covered in ``test_todo_ui``; what is left is the behaviour the
widget adds on top: it hides when there is nothing to show, it animates only
while a task is running, and it repaints on resize.
"""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult

from vtx.coding_agent.tools.todo import TodoTool, current_todo_tool
from vtx.tui.todo_panel import IDLE_TICK_SECONDS, TICK_SECONDS, TodoPanel


class _PanelApp(App):
    def __init__(self, tool: TodoTool) -> None:
        super().__init__()
        self._tool = tool

    def compose(self) -> ComposeResult:
        yield TodoPanel(id="todo-panel")

    def on_mount(self) -> None:
        # ``current_todo_tool`` is a module-level singleton the tool sets on
        # construction, so the panel picks up whichever tool the session built.
        assert current_todo_tool() is self._tool


async def tool_add(subject: str) -> None:
    """Build a fresh session with one pending task.

    ``current_todo_tool`` is a module-level singleton that each ``TodoTool``
    replaces on construction, so each test starts from a tool that has never
    seen another test's tasks.
    """
    from vtx.coding_agent.tools.todo import TodoParams

    tool = TodoTool()
    await tool.execute(TodoParams(action="create", subject=subject))


@pytest.mark.asyncio
async def test_panel_is_hidden_without_tasks() -> None:
    tool = TodoTool()
    app = _PanelApp(tool)
    async with app.run_test() as pilot:
        panel = app.query_one("#todo-panel", TodoPanel)
        await pilot.pause()
        assert not panel.has_class("-visible")
        assert panel.render().plain == ""


@pytest.mark.asyncio
async def test_panel_shows_once_a_task_exists() -> None:
    await tool_add("Read the code")
    app = _PanelApp(current_todo_tool())
    async with app.run_test() as pilot:
        panel = app.query_one("#todo-panel", TodoPanel)
        await pilot.pause()
        assert panel.has_class("-visible")
        assert "Read the code" in panel.render().plain


@pytest.mark.asyncio
async def test_panel_hides_again_when_the_plan_is_cleared() -> None:
    from vtx.coding_agent.tools.todo import TodoParams

    await tool_add("Read the code")
    app = _PanelApp(current_todo_tool())
    async with app.run_test() as pilot:
        panel = app.query_one("#todo-panel", TodoPanel)
        await pilot.pause()
        assert panel.has_class("-visible")

        tool = current_todo_tool()
        assert tool is not None
        await tool.execute(TodoParams(action="clear"))
        panel.refresh_panel()
        await pilot.pause()
        assert not panel.has_class("-visible")
        # A hidden strip must also stop its timer, or it repaints forever
        # drawing nothing.
        assert panel._timer is None


@pytest.mark.asyncio
async def test_timer_runs_fast_only_while_a_task_is_running() -> None:
    from vtx.coding_agent.tools.todo import TodoParams

    tool = TodoTool()
    await tool.execute(TodoParams(action="create", subject="Idle task"))

    app = _PanelApp(tool)
    async with app.run_test() as pilot:
        panel = app.query_one("#todo-panel", TodoPanel)
        await pilot.pause()
        # Nothing is running, so the strip must not spin a widget that has
        # nothing to animate.
        assert panel._timer_interval == IDLE_TICK_SECONDS

        await tool.execute(TodoParams(action="update", id=1, status="in_progress"))
        panel.refresh_panel()
        await pilot.pause()
        assert panel._timer_interval == TICK_SECONDS


@pytest.mark.asyncio
async def test_counts_mirrors_the_plan() -> None:
    from vtx.coding_agent.tools.todo import TodoParams

    tool = TodoTool()
    await tool.execute(TodoParams(action="create", subject="A"))
    await tool.execute(TodoParams(action="create", subject="B"))
    await tool.execute(TodoParams(action="update", id=1, status="completed"))

    app = _PanelApp(tool)
    async with app.run_test() as pilot:
        panel = app.query_one("#todo-panel", TodoPanel)
        await pilot.pause()
        assert panel.counts == (1, 2)


@pytest.mark.asyncio
async def test_refresh_after_a_tool_call_paints_the_new_row() -> None:
    """The tool calls the registered refresh callback, not the panel itself."""
    from vtx.coding_agent.tools.todo import (
        TodoParams,
        register_todo_refresh,
        unregister_todo_refresh,
    )

    tool = TodoTool()
    app = _PanelApp(tool)
    async with app.run_test() as pilot:
        panel = app.query_one("#todo-panel", TodoPanel)
        register_todo_refresh(panel.refresh_panel)
        try:
            await pilot.pause()
            await tool.execute(TodoParams(action="create", subject="Painted row"))
            await pilot.pause()
            assert "Painted row" in panel.render().plain
        finally:
            unregister_todo_refresh(panel.refresh_panel)

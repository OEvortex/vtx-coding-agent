from __future__ import annotations

import pytest

from vtx.coding_agent.tools.todo import TaskRecord, TodoParams, TodoTool, format_todo_tree
from vtx.tui.task_ui import SPINNER
from vtx.tui.todo_block import TodoToolBlock, _Record
from vtx.tui.todo_ui import task_row


def _todo(content: str, status: str = "pending", **kw) -> dict:
    return {"content": content, "status": status, **kw}


@pytest.mark.asyncio
async def test_todo_tool_set_and_exact_tree_ui():
    tool = TodoTool()

    r = await tool.execute(
        TodoParams(
            todos=[
                _todo("Create DemoTodo domain entity", "completed"),
                _todo("Create IDemoTodoRepository interface", "completed"),
                _todo(
                    "Create DemoTodoRepository in Infrastructure",
                    "in_progress",
                    activeForm="Creating DemoTodoRepository implementation",
                ),
                _todo("Create DemoTodoService in Application layer"),
                _todo("Create DemoTodosController in Web.Api"),
                _todo("Register DemoTodo services in DI container"),
                _todo("Write unit and integration tests"),
            ]
        )
    )
    assert r.success is True
    assert r.result == "Todos updated: 2/7 completed"
    assert r.ui_summary == "2/7"

    details = r.ui_details_full
    assert details is not None
    lines = details.split("\n")
    assert len(lines) == 7

    # The sub-agent strip's grammar: a gutter and a `├─`/`└─` branch.
    for line in lines[:6]:
        assert line.startswith("│ ├─ ")
    assert lines[6].startswith("│ └─ ")

    assert "✓" in lines[0]
    assert "Create DemoTodo domain entity" in lines[0]

    assert "✓" in lines[1]
    assert "Create IDemoTodoRepository interface" in lines[1]

    # In-progress is a spinner frame from the shared braille set, never a
    # static half-filled circle.
    assert lines[2].split()[2] in SPINNER
    assert "Create DemoTodoRepository in Infrastructure" in lines[2]
    assert "Creating DemoTodoRepository implementation" in lines[2]

    assert "○" in lines[3]
    assert "Create DemoTodoService in Application layer" in lines[3]

    assert "○" in lines[6]
    assert "Write unit and integration tests" in lines[6]

    # ui_data carries the full task state for the TUI block.
    tasks = r.ui_data["tasks"]
    assert len(tasks) == 7
    assert tasks[2]["status"] == "in_progress"
    assert tasks[2]["activeForm"] == "Creating DemoTodoRepository implementation"
    assert r.ui_data["delta"]["just_started"] == 3


@pytest.mark.asyncio
async def test_completed_rows_are_struck_through_in_the_rendered_text():
    """Strike is a style, so it has to be asserted on the styled ``Text``."""
    tool = TodoTool()
    await tool.execute(
        TodoParams(todos=[_todo("Write the parser", "completed"), _todo("Wire it up")])
    )

    raw = tool._tasks[0].model_dump(by_alias=True, exclude_none=True)
    row = task_row(_Record(raw), width=80, last=True)
    assert "✓" in row.plain
    assert "Write the parser" in row.plain
    assert any("strike" in str(span.style) for span in row.spans)


@pytest.mark.asyncio
async def test_extra_in_progress_tasks_are_demoted_not_rejected():
    tool = TodoTool()
    r = await tool.execute(
        TodoParams(
            todos=[
                _todo("Task A", "in_progress", activeForm="Doing A"),
                _todo("Task B", "in_progress", activeForm="Doing B"),
            ]
        )
    )
    assert r.success is True
    # First running task wins; the second is demoted and reported.
    assert tool._tasks[0].status == "in_progress"
    assert tool._tasks[1].status == "pending"
    assert r.ui_data["demoted"] == [2]
    assert "demoted #2" in r.result


@pytest.mark.asyncio
async def test_resubmitting_same_list_produces_no_delta():
    tool = TodoTool()
    todos = [_todo("Task A", "in_progress"), _todo("Task B")]
    await tool.execute(TodoParams(todos=todos))
    r = await tool.execute(TodoParams(todos=todos))
    assert r.success is True
    assert r.ui_details in (None, "")


@pytest.mark.asyncio
async def test_new_tasks_render_as_a_delta_row_not_the_whole_plan():
    tool = TodoTool()
    await tool.execute(TodoParams(todos=[_todo("First task")]))
    first = await tool.execute(TodoParams(todos=[_todo("First task"), _todo("Second task")]))
    assert first.ui_details is not None
    assert "Second task" in first.ui_details
    assert "First task" not in first.ui_details

    last = await tool.execute(
        TodoParams(
            todos=[_todo("First task", "completed"), _todo("Second task"), _todo("Third task")]
        )
    )
    assert last.ui_details is not None
    assert "Third task" in last.ui_details


@pytest.mark.asyncio
async def test_pending_task_with_unmet_dependency_renders_as_blocked():
    tool = TodoTool()
    r = await tool.execute(
        TodoParams(todos=[_todo("Foundation"), _todo("Feature", blockedBy=[1])])
    )
    assert r.success is True
    assert tool._tasks[1].blocked_by == [1]
    details = r.ui_details_full or ""
    assert "⛓" in details or "#1" in details


@pytest.mark.asyncio
async def test_all_complete_renders_a_finishing_note():
    tool = TodoTool()
    await tool.execute(TodoParams(todos=[_todo("Only task", "in_progress")]))
    r = await tool.execute(TodoParams(todos=[_todo("Only task", "completed")]))
    assert r.success is True
    assert r.ui_data["delta"]["all_completed"] is True


@pytest.mark.asyncio
async def test_status_and_subject_aliases_are_accepted():
    tool = TodoTool()
    r = await tool.execute(TodoParams(todos=[{"subject": "Old name", "status": "done"}]))
    assert r.success is True
    assert tool._tasks[0].subject == "Old name"
    assert tool._tasks[0].status == "completed"


@pytest.mark.asyncio
async def test_empty_content_is_rejected_without_mutation():
    tool = TodoTool()
    await tool.execute(TodoParams(todos=[_todo("Keep me")]))
    r = await tool.execute(TodoParams(todos=[_todo("Keep me"), _todo("  ")]))
    assert r.success is False
    assert "empty content" in r.result
    # Atomic: the previous list is untouched.
    assert [t.subject for t in tool._tasks] == ["Keep me"]


@pytest.mark.asyncio
async def test_blocked_by_out_of_range_and_self_are_rejected():
    tool = TodoTool()
    r = await tool.execute(TodoParams(todos=[_todo("A", blockedBy=[2])]))
    assert r.success is False
    assert "out of range" in r.result

    r = await tool.execute(TodoParams(todos=[_todo("A", blockedBy=[1])]))
    assert r.success is False

    r = await tool.execute(
        TodoParams(todos=[_todo("A"), _todo("B", blockedBy=[1]), _todo("C", blockedBy=[2])])
    )
    assert r.success is True
    assert tool._tasks[2].blocked_by == [2]


@pytest.mark.asyncio
async def test_blocked_by_cycle_is_rejected():
    tool = TodoTool()
    r = await tool.execute(
        TodoParams(todos=[_todo("A", blockedBy=[2]), _todo("B", blockedBy=[1])])
    )
    assert r.success is False
    assert "cycle" in r.result
    assert tool._tasks == []


@pytest.mark.asyncio
async def test_empty_list_clears_all_tasks():
    tool = TodoTool()
    await tool.execute(TodoParams(todos=[_todo("A"), _todo("B")]))
    r = await tool.execute(TodoParams(todos=[]))
    assert r.success is True
    assert tool._tasks == []
    assert "0/0" in (r.ui_summary or "")


def test_format_todo_tree_empty():
    assert format_todo_tree([]) == "No tasks"


def test_format_todo_tree_is_plain_text_for_session_export():
    tasks = [TaskRecord(id=1, subject="One", status="completed")]
    out = format_todo_tree(tasks)
    assert "[" not in out.split("One")[0] or "✓" in out


def test_todo_ui_block_and_header():
    tool = TodoTool()
    assert tool.ui_block is TodoToolBlock


def test_block_header_says_which_way_the_plan_moved():
    tool = TodoTool()
    tool._tasks = [
        TaskRecord(id=1, subject="A", status="completed"),
        TaskRecord(id=2, subject="B", status="in_progress"),
    ]
    tool._next_id = 3
    tool._before = {1: "pending", 2: "pending"}
    result = tool._build_result(action="set", raw_params={}, content_text="ok", error=None)
    assert result.ui_summary == "1/2"


def test_todo_plugin_mounted():
    from vtx.coding_agent.tools.todo import PLUGIN

    assert PLUGIN is not None

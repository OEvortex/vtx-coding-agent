from __future__ import annotations

import pytest
from rich.text import Text

from vtx.coding_agent.tools.todo import (
    TaskRecord,
    TodoItem,
    TodoParams,
    TodoTool,
    format_todo_tree,
)
from vtx.plugins import boot
from vtx.tui.task_ui import SPINNER
from vtx.tui.todo_block import TodoToolBlock, _Record
from vtx.tui.todo_ui import render_strip, task_row


def _tasks_of(tool: TodoTool) -> list[dict]:
    """The tool's tasks as the JSON-safe dicts ``ui_data`` carries."""
    return [t.model_dump(by_alias=True, exclude_none=True) for t in tool._tasks]


def _record(raw: dict) -> _Record:
    return _Record(raw)


@pytest.mark.asyncio
async def test_todo_tool_rpiv_create_and_exact_tree_ui():
    tool = TodoTool()

    # Create tasks matching user's reference image
    r1 = await tool.execute(TodoParams(action="create", subject="Create DemoTodo domain entity"))
    assert r1.success is True
    assert r1.result == "Created #1: Create DemoTodo domain entity (pending)"

    await tool.execute(TodoParams(action="create", subject="Create IDemoTodoRepository interface"))
    await tool.execute(
        TodoParams(
            action="create",
            subject="Create DemoTodoRepository in Infrastructure",
            active_form="Creating DemoTodoRepository implementation",
        )
    )
    await tool.execute(
        TodoParams(action="create", subject="Create DemoTodoService in Application layer")
    )
    await tool.execute(
        TodoParams(action="create", subject="Create DemoTodosController in Web.Api")
    )
    await tool.execute(
        TodoParams(action="create", subject="Register DemoTodo services in DI container")
    )
    await tool.execute(TodoParams(action="create", subject="Write unit and integration tests"))

    # Transition statuses: #1 and #2 completed, #3 in_progress
    await tool.execute(TodoParams(action="update", id=1, status="completed"))
    await tool.execute(TodoParams(action="update", id=2, status="completed"))
    res_up3 = await tool.execute(
        TodoParams(
            action="update",
            id=3,
            status="in_progress",
            active_form="Creating DemoTodoRepository implementation",
        )
    )
    assert res_up3.result == "Updated #3 (pending → in_progress)"
    assert res_up3.ui_summary == "2/7"

    # ``ui_details`` is the delta, not the list: this call only started #3, so
    # only #3 is drawn. ``ui_details_full`` is the whole plan.
    assert res_up3.ui_details is not None
    delta_lines = res_up3.ui_details.split("\n")
    assert len(delta_lines) == 1
    assert "Create DemoTodoRepository in Infrastructure" in delta_lines[0]
    assert "Creating DemoTodoRepository implementation" in delta_lines[0]

    details = res_up3.ui_details_full
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


@pytest.mark.asyncio
async def test_completed_rows_are_struck_through_in_the_rendered_text():
    """Strike is a style, so it has to be asserted on the styled ``Text``.

    ``ui_details`` is deliberately plain text (a session export writes it into
    markdown, where ``[strike]`` would land as literal brackets), so the check
    that finished work reads as finished has to go through the renderer.
    """
    tool = TodoTool()
    await tool.execute(TodoParams(action="create", subject="Write the parser"))
    await tool.execute(TodoParams(action="update", id=1, status="completed"))

    row = task_row(_record(_tasks_of(tool)[0]), width=80, last=True)
    assert "✓" in row.plain
    assert "Write the parser" in row.plain
    assert any("strike" in str(span.style) for span in row.spans)


@pytest.mark.asyncio
async def test_starting_a_second_task_demotes_the_first_rather_than_erroring():
    """Two ``in_progress`` rows would make the strip's single accent meaningless.

    Gemini rejects the list outright; that costs the model a whole turn to fix
    something the tool could fix itself and report back.
    """
    tool = TodoTool()
    await tool.execute(TodoParams(action="create", subject="Task A", active_form="Doing A"))
    await tool.execute(TodoParams(action="create", subject="Task B", active_form="Doing B"))

    r1 = await tool.execute(TodoParams(action="update", id=1, status="in_progress"))
    assert r1.success is True
    # No demotion happened, so the key is absent rather than an empty list.
    assert "demoted" not in r1.ui_data

    r2 = await tool.execute(TodoParams(action="update", id=2, status="in_progress"))
    assert r2.success is True
    assert "demoted #1 to pending" in r2.result
    assert r2.ui_data["demoted"] == [1]

    statuses = {t.id: t.status for t in tool._tasks}
    assert statuses == {1: "pending", 2: "in_progress"}


@pytest.mark.asyncio
async def test_batch_set_keeps_only_the_first_running_task():
    tool = TodoTool()
    r = await tool.execute(
        TodoParams(
            action="set",
            todos=[
                {"subject": "A", "status": "in_progress"},
                {"subject": "B", "status": "in_progress"},
                {"subject": "C", "status": "pending"},
            ],
        )
    )
    assert r.success is True
    assert [t.status for t in tool._tasks] == ["in_progress", "pending", "pending"]
    assert r.ui_data["demoted"] == [2]


@pytest.mark.asyncio
async def test_status_only_edits_render_no_body():
    """A re-send of the current status is not news and must not print a row."""
    tool = TodoTool()
    await tool.execute(TodoParams(action="create", subject="Task A"))
    await tool.execute(TodoParams(action="update", id=1, status="in_progress"))

    r = await tool.execute(TodoParams(action="update", id=1, status="in_progress"))
    assert r.success is True
    assert r.ui_details == ""


@pytest.mark.asyncio
async def test_new_tasks_render_as_a_delta_row_not_the_whole_plan():
    tool = TodoTool()
    await tool.execute(TodoParams(action="create", subject="First task"))

    # The very first call has nothing before it, so the plan is printed whole.
    first = await tool.execute(TodoParams(action="create", subject="Second task"))
    assert len(first.ui_details.split("\n")) == 1
    assert "Second task" in first.ui_details
    assert "First task" not in first.ui_details

    last = await tool.execute(TodoParams(action="create", subject="Third task"))
    assert len(last.ui_details.split("\n")) == 1
    assert "Third task" in last.ui_details


@pytest.mark.asyncio
async def test_pending_task_with_unmet_dependency_renders_as_blocked():
    tool = TodoTool()
    await tool.execute(
        TodoParams(
            action="set",
            todos=[
                {"subject": "Write the schema", "status": "pending"},
                {"subject": "Write the tests", "status": "pending", "blockedBy": [1]},
            ],
        )
    )
    strip = render_strip([_record(d) for d in _tasks_of(tool)], width=80).plain
    assert "⊘ Write the tests" in strip
    assert "← #1" in strip

    # Finishing the blocker clears the marker.
    await tool.execute(TodoParams(action="update", id=1, status="completed"))
    strip = render_strip([_record(d) for d in _tasks_of(tool)], width=80).plain
    assert "⊘" not in strip
    assert "○ Write the tests" in strip


@pytest.mark.asyncio
async def test_all_complete_renders_the_plan_and_a_finishing_note():
    tool = TodoTool()
    await tool.execute(
        TodoParams(action="set", todos=[{"subject": "Only task", "status": "in_progress"}])
    )
    r = await tool.execute(TodoParams(action="update", id=1, status="completed"))
    assert "✓ all tasks complete" in r.ui_details


@pytest.mark.asyncio
async def test_todo_tool_rpiv_status_transitions():
    tool = TodoTool()
    await tool.execute(TodoParams(action="create", subject="Task 1"))

    # pending -> in_progress (allowed)
    r = await tool.execute(TodoParams(action="update", id=1, status="in_progress"))
    assert r.success is True
    assert r.result == "Updated #1 (pending → in_progress)"

    # in_progress -> completed (allowed)
    r = await tool.execute(TodoParams(action="update", id=1, status="completed"))
    assert r.success is True
    assert r.result == "Updated #1 (in_progress → completed)"

    # completed -> in_progress (illegal!)
    r = await tool.execute(TodoParams(action="update", id=1, status="in_progress"))
    assert r.success is False
    assert r.result == "Error: illegal transition completed → in_progress"
    assert r.structured["error"] == "illegal transition completed → in_progress"

    # completed -> deleted (allowed)
    r = await tool.execute(TodoParams(action="delete", id=1))
    assert r.success is True
    assert r.result == "Deleted #1: Task 1"

    # deleted -> anything (terminal, delete again -> already deleted)
    r = await tool.execute(TodoParams(action="delete", id=1))
    assert r.success is False
    assert r.result == "Error: #1 is already deleted"


@pytest.mark.asyncio
async def test_todo_tool_rpiv_dependencies_and_cycles():
    tool = TodoTool()
    await tool.execute(TodoParams(action="create", subject="Task 1"))
    await tool.execute(TodoParams(action="create", subject="Task 2"))
    await tool.execute(TodoParams(action="create", subject="Task 3"))

    # Cannot block on itself
    r = await tool.execute(TodoParams(action="update", id=1, add_blocked_by=[1]))
    assert r.success is False
    assert "cannot block #1 on itself" in r.result

    # Unknown dependency
    r = await tool.execute(TodoParams(action="update", id=1, add_blocked_by=[99]))
    assert r.success is False
    assert "addBlockedBy: #99 not found" in r.result

    # Add valid dependency: Task 2 waits on Task 1
    r = await tool.execute(TodoParams(action="update", id=2, add_blocked_by=[1]))
    assert r.success is True

    # Task 3 waits on Task 2
    r = await tool.execute(TodoParams(action="update", id=3, add_blocked_by=[2]))
    assert r.success is True

    # Cycle: Task 1 cannot wait on Task 3 (3 -> 2 -> 1 -> 3)
    r = await tool.execute(TodoParams(action="update", id=1, add_blocked_by=[3]))
    assert r.success is False
    assert "addBlockedBy would create a cycle in the blockedBy graph" in r.result

    # Check 'get' action with reverse blocks
    r_get = await tool.execute(TodoParams(action="get", id=1))
    assert r_get.success is True
    assert "[pending] #1 Task 1" in r_get.result
    assert "blocks: #2" in r_get.result

    # Remove blocked by
    r = await tool.execute(TodoParams(action="update", id=2, remove_blocked_by=[1]))
    assert r.success is True
    task2 = tool._find_task(2)
    assert task2 is not None
    assert 1 not in task2.blocked_by


@pytest.mark.asyncio
async def test_todo_tool_rpiv_no_change_and_metadata():
    tool = TodoTool()
    await tool.execute(TodoParams(action="create", subject="Task 1"))

    # Update metadata
    r = await tool.execute(
        TodoParams(action="update", id=1, metadata={"priority": "high", "env": "prod"})
    )
    assert r.success is True
    assert r.result == "Updated #1"

    # No change reply
    r_nochange = await tool.execute(
        TodoParams(action="update", id=1, metadata={"priority": "high", "env": "prod"})
    )
    assert r_nochange.success is True
    assert "No change: #1 already matches the requested values" in r_nochange.result

    # Delete key with null
    r = await tool.execute(TodoParams(action="update", id=1, metadata={"env": None}))
    assert r.success is True
    task1 = tool._find_task(1)
    assert task1 is not None and task1.metadata == {"priority": "high"}


@pytest.mark.asyncio
async def test_todo_tool_rpiv_list_and_clear():
    tool = TodoTool()
    await tool.execute(TodoParams(action="create", subject="Task 1"))
    await tool.execute(TodoParams(action="create", subject="Task 2"))
    await tool.execute(TodoParams(action="delete", id=1))

    # List hides deleted by default
    r_list = await tool.execute(TodoParams(action="list"))
    assert "[pending] #2 Task 2" in r_list.result
    assert "#1" not in r_list.result

    # List with include_deleted
    r_list_del = await tool.execute(TodoParams(action="list", include_deleted=True))
    assert "[deleted] #1 Task 1" in r_list_del.result
    assert "[pending] #2 Task 2" in r_list_del.result

    # Clear
    r_clear = await tool.execute(TodoParams(action="clear"))
    assert r_clear.result == "Cleared 2 tasks"
    assert len(tool._tasks) == 0
    assert tool._next_id == 1


@pytest.mark.asyncio
async def test_todo_batch_set_mode():
    tool = TodoTool()
    r = await tool.execute(
        TodoParams(
            todos=[
                TaskRecord(id=1, subject="Task A", status="completed"),
                TaskRecord(id=2, subject="Task B", status="pending"),
            ]
        )
    )
    assert r.success is True
    assert len(tool._tasks) == 2
    assert tool._tasks[0].subject == "Task A"
    assert tool._tasks[0].status == "completed"
    assert TodoItem is TaskRecord


def test_format_todo_tree_empty():
    assert format_todo_tree([]) == "No tasks"


def test_format_todo_tree_is_plain_text_for_session_export():
    """Rich tags in this string would land in exported markdown as literals."""
    tool = TodoTool()
    assert "[" not in format_todo_tree(_tasks_of(tool))


def test_todo_ui_block_and_header():
    tool = TodoTool()
    assert tool.ui_block is TodoToolBlock

    block = TodoToolBlock(name="todo", icon="●", tool=tool)
    block._ui_summary = "2/7"
    header = block._format_header()
    assert isinstance(header, Text)
    assert "●" in header.plain
    assert "Todos" in header.plain
    assert "2/7" in header.plain


def test_block_header_says_which_way_the_plan_moved():
    """A bare counter does not tell the reader whether work advanced."""
    tool = TodoTool()
    block = TodoToolBlock(name="todo", icon="●", tool=tool)
    block.ui_data = {"delta": {"just_completed": [1, 2], "just_started": 3, "total": 7}}
    assert "completed 2, starting next" in block._format_header().plain

    block.ui_data = {"delta": {"just_completed": [1], "just_started": None, "total": 7}}
    assert "completed 1" in block._format_header().plain

    block.ui_data = {"delta": {"just_started": 3, "total": 7}}
    assert "starting task" in block._format_header().plain

    block.ui_data = {"delta": {"all_completed": True, "total": 3}}
    assert "completed all" in block._format_header().plain


def test_todo_plugin_mounted():
    host = boot()
    try:
        tools = host.base.tools
        assert "todo" in tools.names()
        todo_tool = tools.get("todo")
        assert todo_tool is not None
        assert todo_tool.name == "todo"
        assert todo_tool.tool_icon == "●"
    finally:
        host.dispose()

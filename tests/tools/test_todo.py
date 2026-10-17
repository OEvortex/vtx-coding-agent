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
from vtx.tui.todo_block import TodoToolBlock


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
    assert res_up3.ui_summary == "(2/7)"

    details = res_up3.ui_details
    assert details is not None
    lines = details.split("\n")
    assert len(lines) == 7

    # Check tree branch symbols
    for line in lines[:6]:
        assert "├── " in line
    assert "└── " in lines[6]

    # Check status symbols and formatting
    assert "✓" in lines[0]
    assert "strike" in lines[0]
    assert "Create DemoTodo domain entity" in lines[0]

    assert "✓" in lines[1]
    assert "strike" in lines[1]
    assert "Create IDemoTodoRepository interface" in lines[1]

    assert "◐" in lines[2]
    assert "Create DemoTodoRepository in Infrastructure" in lines[2]
    assert "(Creating DemoTodoRepository implementation)" in lines[2]

    assert "○" in lines[3]
    assert "Create DemoTodoService in Application layer" in lines[3]

    assert "○" in lines[6]
    assert "Write unit and integration tests" in lines[6]


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
    assert format_todo_tree([]) == "[dim]No tasks[/dim]"


def test_todo_ui_block_and_header():
    tool = TodoTool()
    assert tool.ui_block is TodoToolBlock

    block = TodoToolBlock(name="todo", icon="●", tool=tool)
    block._ui_summary = "(2/7)"
    header = block._format_header()
    assert isinstance(header, Text)
    assert "●" in header.plain
    assert "Todos" in header.plain
    assert "(2/7)" in header.plain


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

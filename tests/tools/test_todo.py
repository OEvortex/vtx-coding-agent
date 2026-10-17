from __future__ import annotations

import pytest
from rich.text import Text

from vtx.coding_agent.tools.todo import TodoItem, TodoParams, TodoTool, format_todo_tree
from vtx.plugins import boot
from vtx.tui.todo_block import TodoToolBlock


@pytest.mark.asyncio
async def test_todo_tool_execution_with_exact_user_spec():
    tool = TodoTool()
    params = TodoParams(
        todos=[
            TodoItem(content="Create DemoTodo domain entity", status="completed"),
            TodoItem(content="Create IDemoTodoRepository interface", status="completed"),
            TodoItem(
                content="Create DemoTodoRepository in Infrastructure",
                status="in_progress",
                active_form="Creating DemoTodoRepository implementation",
            ),
            TodoItem(content="Create DemoTodoService in Application layer", status="pending"),
            TodoItem(content="Create DemoTodosController in Web.Api", status="pending"),
            TodoItem(content="Register DemoTodo services in DI container", status="pending"),
            TodoItem(content="Write unit and integration tests", status="pending"),
        ]
    )

    result = await tool.execute(params)
    assert result.success is True
    assert result.ui_summary == "(2/7)"

    details = result.ui_details
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

    # Check plain LLM result
    assert "Todos (2/7):" in result.result
    assert "- [x] Create DemoTodo domain entity" in result.result
    assert "- [x] Create IDemoTodoRepository interface" in result.result
    assert (
        "- [/] Create DemoTodoRepository in Infrastructure "
        "(Creating DemoTodoRepository implementation)" in result.result
    )
    assert "- [ ] Write unit and integration tests" in result.result


@pytest.mark.asyncio
async def test_todo_item_coercions():
    # Test field name aliasing (title, task, description)
    item1 = TodoItem.model_validate({"title": "Task A", "status": "done"})
    assert item1.content == "Task A"
    assert item1.status == "completed"

    item2 = TodoItem.model_validate({"task": "Task B", "status": "wip", "activeForm": "Doing B"})
    assert item2.content == "Task B"
    assert item2.status == "in_progress"
    assert item2.active_form == "Doing B"

    item3 = TodoItem.model_validate({"description": "Task C", "status": "todo"})
    assert item3.content == "Task C"
    assert item3.status == "pending"


@pytest.mark.asyncio
async def test_todo_single_item_actions():
    tool = TodoTool()

    # Add item
    await tool.execute(TodoParams(action="add", content="First task"))
    assert len(tool._todos) == 1
    assert tool._todos[0].content == "First task"
    assert tool._todos[0].status == "pending"

    # Add second item
    await tool.execute(TodoParams(action="add", content="Second task"))
    assert len(tool._todos) == 2

    # Update item by content
    await tool.execute(
        TodoParams(
            action="update",
            content="First task",
            status="in_progress",
            active_form="Working on first task",
        )
    )
    assert tool._todos[0].status == "in_progress"
    assert tool._todos[0].active_form == "Working on first task"

    # Update item to completed
    await tool.execute(TodoParams(action="update", content="First task", status="completed"))
    assert tool._todos[0].status == "completed"

    # Delete item
    await tool.execute(TodoParams(action="delete", content="Second task"))
    assert len(tool._todos) == 1
    assert tool._todos[0].content == "First task"

    # Clear
    await tool.execute(TodoParams(action="clear"))
    assert len(tool._todos) == 0


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

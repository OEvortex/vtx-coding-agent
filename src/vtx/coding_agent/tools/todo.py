from __future__ import annotations

import asyncio
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator
from rich.markup import escape

from vtx.agent.tools.base import BaseTool
from vtx.core.config import config
from vtx.plugins import mount_tool, plugin
from vtx.protocol.types import ToolResult


class TodoItem(BaseModel):
    id: str | None = None
    content: str
    status: Literal["pending", "in_progress", "completed"] = "pending"
    active_form: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _coerce_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        if "content" not in data:
            for alt in ("title", "task", "description", "text"):
                if alt in data:
                    data["content"] = data[alt]
                    break
        if "content" not in data:
            data["content"] = ""
        if "active_form" not in data and "activeForm" in data:
            data["active_form"] = data["activeForm"]

        raw_status = (
            str(data.get("status", "pending")).lower().strip().replace("-", "_").replace(" ", "_")
        )
        if raw_status in ("completed", "done", "finished", "complete", "closed"):
            data["status"] = "completed"
        elif raw_status in ("in_progress", "progress", "active", "doing", "wip", "started"):
            data["status"] = "in_progress"
        else:
            data["status"] = "pending"
        return data


class TodoParams(BaseModel):
    todos: list[TodoItem] | None = Field(
        default=None, description="Full list of todo items to replace/sync current list."
    )
    action: Literal["set", "add", "update", "delete", "remove", "clear", "list"] | None = Field(
        default=None,
        description="Action to perform: 'set', 'add', 'update', 'delete', 'clear', or 'list'",
    )
    content: str | None = Field(
        default=None, description="Task description (for add or update action)"
    )
    status: Literal["pending", "in_progress", "completed"] | None = Field(
        default=None, description="Status to set ('pending', 'in_progress', 'completed')"
    )
    active_form: str | None = Field(
        default=None,
        description=("Present continuous description of active task (e.g. 'Creating repository')"),
    )
    id: str | None = Field(
        default=None, description="Optional ID or 1-based index of item to update or delete"
    )


def format_todo_tree(todos: list[TodoItem]) -> str:
    if not todos:
        return "[dim]No tasks[/dim]"

    try:
        colors = config.ui.colors
        success_col = colors.success
        notice_col = colors.notice
        dim_col = colors.dim
    except Exception:
        success_col = "#00ff88"
        notice_col = "#ffb347"
        dim_col = "#6b7280"

    lines: list[str] = []
    total = len(todos)
    for i, item in enumerate(todos):
        is_last = i == total - 1
        branch = "└── " if is_last else "├── "
        content_escaped = escape(item.content)
        branch_markup = f"[{dim_col}]{branch}[/{dim_col}]"

        if item.status == "completed":
            icon_markup = f"[{success_col}]✓[/{success_col}]"
            task_markup = f"[strike {dim_col}]{content_escaped}[/strike {dim_col}]"
            lines.append(f"{branch_markup}{icon_markup} {task_markup}")
        elif item.status == "in_progress":
            icon_markup = f"[{notice_col}]◐[/{notice_col}]"
            active_markup = (
                f" [{dim_col}]({escape(item.active_form)})[/{dim_col}]" if item.active_form else ""
            )
            task_markup = f"{content_escaped}{active_markup}"
            lines.append(f"{branch_markup}{icon_markup} {task_markup}")
        else:
            icon_markup = f"[{dim_col}]○[/{dim_col}]"
            lines.append(f"{branch_markup}{icon_markup} {content_escaped}")

    return "\n".join(lines)


class TodoTool(BaseTool):
    name = "todo"
    tool_icon = "●"
    params = TodoParams
    mutating = False
    prompt_guidelines = ("todo to track, plan, and update tasks during multi-step work",)
    description = (
        "Track and update progress on multi-step tasks. "
        "Pass 'todos' with an array of tasks (with 'content', "
        "'status': 'pending'|'in_progress'|'completed', "
        "and optional 'active_form' for in_progress items) to record or update the task plan."
    )

    def __init__(self) -> None:
        super().__init__()
        self._todos: list[TodoItem] = []

    def format_call(self, params: TodoParams) -> str:
        return ""

    @property
    def ui_block(self) -> type | None:
        try:
            from vtx.tui.todo_block import TodoToolBlock

            return TodoToolBlock
        except ImportError:
            return None

    async def execute(
        self, params: TodoParams, cancel_event: asyncio.Event | None = None
    ) -> ToolResult:
        action = params.action

        if params.todos is not None:
            self._todos = list(params.todos)
        elif action == "add" or (action is None and params.content and not params.status):
            if params.content:
                self._todos.append(
                    TodoItem(
                        id=params.id,
                        content=params.content,
                        status=params.status or "pending",
                        active_form=params.active_form,
                    )
                )
        elif action == "update" or (action is None and params.content and params.status):
            target_idx: int | None = None
            if params.id is not None:
                try:
                    num = int(params.id)
                    if 1 <= num <= len(self._todos):
                        target_idx = num - 1
                except ValueError:
                    for i, t in enumerate(self._todos):
                        if t.id == params.id:
                            target_idx = i
                            break
            if target_idx is None and params.content:
                needle = params.content.strip().lower()
                for i, t in enumerate(self._todos):
                    if needle in t.content.lower():
                        target_idx = i
                        break

            if target_idx is not None:
                current = self._todos[target_idx]
                new_status = params.status or current.status
                new_active = (
                    params.active_form if params.active_form is not None else current.active_form
                )
                new_content = (
                    params.content if (params.content and action == "update") else current.content
                )
                self._todos[target_idx] = TodoItem(
                    id=current.id or params.id,
                    content=new_content,
                    status=new_status,
                    active_form=new_active,
                )
        elif action in ("delete", "remove"):
            if params.id is not None:
                try:
                    num = int(params.id)
                    if 1 <= num <= len(self._todos):
                        self._todos.pop(num - 1)
                except ValueError:
                    self._todos = [t for t in self._todos if t.id != params.id]
            elif params.content:
                needle = params.content.strip().lower()
                self._todos = [t for t in self._todos if needle not in t.content.lower()]
        elif action == "clear":
            self._todos.clear()

        total = len(self._todos)
        completed = sum(1 for t in self._todos if t.status == "completed")
        summary = f"({completed}/{total})"

        ui_details = format_todo_tree(self._todos)

        # Markdown representation for the LLM
        md_lines = [f"Todos ({completed}/{total}):"]
        for t in self._todos:
            if t.status == "completed":
                mark = "[x]"
            elif t.status == "in_progress":
                mark = "[/]"
            else:
                mark = "[ ]"
            active_info = f" ({t.active_form})" if t.active_form else ""
            md_lines.append(f"- {mark} {t.content}{active_info}")

        return ToolResult(
            success=True, result="\n".join(md_lines), ui_summary=summary, ui_details=ui_details
        )


@plugin(name="tool-todo", inject=("tools",))
def apply(ctx, config):
    mount_tool(ctx, TodoTool(), is_default=True, parent_only=False)


PLUGIN = apply

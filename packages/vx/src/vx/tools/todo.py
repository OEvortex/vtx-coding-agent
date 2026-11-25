"""The todo tool: the plan the sidebar draws.

The model sends the whole list every time rather than an operation per row. A
plan is small, and a full list is the one shape that cannot drift out of step
with the panel: whatever the model last said *is* the plan.
"""

import asyncio
from typing import Any, Literal

from ctx.agent.tools.base import BaseTool
from ctx.protocol.types import ToolResult
from pydantic import BaseModel, Field, field_validator, model_validator

#: A plan longer than this is not a plan, it is a transcript in a column.
MAX_TODOS = 20
STATUSES = ("pending", "active", "done", "blocked")

#: What models call each state.
_STATUS_ALIASES = {
    "todo": "pending",
    "open": "pending",
    "not started": "pending",
    "in progress": "active",
    "in_progress": "active",
    "doing": "active",
    "current": "active",
    "started": "active",
    "complete": "done",
    "completed": "done",
    "finished": "done",
    "ok": "done",
    "blocked": "blocked",
    "failed": "blocked",
    "stuck": "blocked",
}

#: What models call the text of an item.
_TEXT_ALIASES = ("text", "task", "title", "description", "content", "name", "label")


def _first_str(item: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _status(value: Any) -> str:
    if not isinstance(value, str):
        return "pending"
    flat = value.strip().lower().replace("-", " ")
    if flat in STATUSES:
        return flat
    return _STATUS_ALIASES.get(flat, _STATUS_ALIASES.get(flat.replace(" ", "_"), "pending"))


class TodoItemParam(BaseModel):
    text: str = Field(default="", description="What the step is, in a few words")
    status: Literal["pending", "active", "done", "blocked"] = Field(
        default="pending", description="pending | active | done | blocked"
    )

    @field_validator("status", mode="before")
    @classmethod
    def _accept_status(cls, value: Any) -> str:
        return _status(value)

    @model_validator(mode="before")
    @classmethod
    def _accept_aliases(cls, value: Any) -> Any:
        """`task`, `title`, `description` … are all the same field."""
        if not isinstance(value, dict):
            return value
        if value.get("text"):
            return value
        found = _first_str(value, _TEXT_ALIASES)
        return {**value, "text": found} if found else value


class TodoParams(BaseModel):
    """The whole plan, each time it changes."""

    todos: list[TodoItemParam] = Field(description="Every step, in order")

    @field_validator("todos", mode="before")
    @classmethod
    def _accept_shapes(cls, value: Any) -> Any:
        """Take the shapes models send: one plan item, a list, or bare strings."""
        if isinstance(value, dict):
            return [value]
        if isinstance(value, str):
            return [{"text": value}]
        if isinstance(value, list):
            return [
                {"text": item} if isinstance(item, str) else item
                for item in value
                if not isinstance(item, str) or item.strip()
            ]
        return value

    def normalized(self) -> list[dict[str, str]]:
        """The plan as the sidebar wants it: non-empty text, known status."""
        out: list[dict[str, str]] = []
        for item in self.todos[:MAX_TODOS]:
            text = " ".join(item.text.split())
            if text:
                out.append({"text": text, "status": _status(item.status)})
        return out


class TodoTool(BaseTool[TodoParams]):
    name = "todo"
    params = TodoParams
    description = (
        "Keep the plan the user can see. Send the whole list on every call, "
        "with the step you are on marked active and finished ones done."
    )
    mutating = False
    parallel_safe = True

    def format_call(self, params: TodoParams) -> str:
        plan = params.normalized()
        if not plan:
            return "clear the plan"
        done = sum(1 for item in plan if item["status"] == "done")
        return f"{done}/{len(plan)} steps"

    async def execute(
        self, params: TodoParams, cancel_event: asyncio.Event | None = None
    ) -> ToolResult:
        return render_plan(params)


def render_plan(params: TodoParams) -> ToolResult:
    """The tool's answer, and the list the panel draws."""
    plan = params.normalized()
    if not plan:
        return ToolResult(
            success=True, result="Plan cleared.", ui_summary="cleared", ui_data={"todos": []}
        )
    counts = {status: 0 for status in STATUSES}
    for item in plan:
        counts[item["status"]] += 1
    lines = [f"[{item['status']}] {item['text']}" for item in plan]
    summary = f"{counts['done']}/{len(plan)} done"
    if counts["active"]:
        active = next(item["text"] for item in plan if item["status"] == "active")
        summary = f"{summary} · {active}"
    return ToolResult(
        success=True,
        result="Plan:\n" + "\n".join(lines),
        ui_summary=summary,
        ui_data={"todos": plan},
    )

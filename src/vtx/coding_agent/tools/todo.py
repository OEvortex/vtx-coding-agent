from __future__ import annotations

import asyncio
import contextlib
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from vtx.agent.tools.base import BaseTool
from vtx.plugins import mount_tool, plugin
from vtx.protocol.types import ToolResult

_current_todo_tool = None
_todo_refresh_callbacks: list = []


def current_todo_tool():
    return _current_todo_tool


def register_todo_refresh(cb):
    if cb not in _todo_refresh_callbacks:
        _todo_refresh_callbacks.append(cb)


def unregister_todo_refresh(cb):
    with contextlib.suppress(ValueError):
        _todo_refresh_callbacks.remove(cb)


StatusType = Literal["pending", "in_progress", "completed", "deleted"]
ItemStatus = Literal["pending", "in_progress", "completed"]


class TaskRecord(BaseModel):
    id: int
    subject: str
    description: str | None = None
    active_form: str | None = Field(default=None, alias="activeForm")
    status: StatusType = "pending"
    blocked_by: list[int] = Field(default_factory=list, alias="blockedBy")
    owner: str | None = None
    metadata: dict[str, Any] | None = None

    model_config = ConfigDict(populate_by_name=True)

    # Alias for backwards compatibility with earlier TodoItem
    @property
    def content(self) -> str:
        return self.subject


# Backwards compatibility alias
TodoItem = TaskRecord


class TodoItemParam(BaseModel):
    """One task in the submitted list. ``blockedBy`` holds 1-based positions of
    sibling items in the same array (position == task id)."""

    model_config = ConfigDict(populate_by_name=True)

    content: str = Field(description="Imperative task title")
    status: ItemStatus = "pending"
    active_form: str | None = Field(
        default=None,
        alias="activeForm",
        description="Present-continuous label shown while in_progress",
    )
    blocked_by: list[int] | None = Field(
        default=None,
        alias="blockedBy",
        description="1-based positions of tasks this one depends on",
    )

    @model_validator(mode="before")
    @classmethod
    def _coerce_item(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        if not data.get("content"):
            for alt in ("subject", "title", "task"):
                if data.get(alt):
                    data["content"] = data[alt]
                    break
        status = str(data.get("status", "")).lower()
        if status in ("done", "complete", "finished"):
            data["status"] = "completed"
        elif status in ("active", "progress", "running"):
            data["status"] = "in_progress"
        return data


class TodoParams(BaseModel):
    todos: list[TodoItemParam] = Field(
        description="The COMPLETE task list — replaces current state"
    )


def format_todo_tree(tasks: list[TaskRecord]) -> str:
    """The task list as plain text, one task per line.

    Plain text with no Rich markup on purpose: this string is what a session
    export writes into its tool-result block, where ``[dim]…[/dim]`` would land
    in the markdown as literal brackets. The TUI does not read it — the todo
    block renders from :mod:`vtx.tui.todo_ui`, which can colour, animate and
    measure the rows instead of flattening them to a string first.
    """
    from vtx.tui.todo_ui import render_list

    if not [t for t in tasks if t.status != "deleted"]:
        return "No tasks"
    return render_list(tasks, width=1000).plain


def _format_todo_delta(delta: dict[str, Any], tasks: list[TaskRecord]) -> str:
    """``ui_details`` for the in-chat block: only what this call changed.

    Empty when the call changed no statuses, which the block renders as no
    body at all. A twenty-task plan updated every few seconds would otherwise
    print its own state two hundred times into the scrollback.
    """
    from vtx.tui.todo_ui import render_delta

    return render_delta(delta, tasks, width=1000).plain


class TodoTool(BaseTool):
    name = "todo"
    tool_icon = "●"
    params = TodoParams
    mutating = False
    prompt_guidelines = (
        "todo: send the COMPLETE todos array each call — it replaces the list, "
        "so plan all steps in your first call",
        "each item: {content, status, activeForm?, blockedBy?}; status is "
        "pending|in_progress|completed",
        "exactly one task in_progress at a time; mark completed immediately when done",
        "skip the todo tool for trivial or conversational work",
    )
    description = (
        "Track multi-step work. Pass the complete todos array "
        "[{content, status, activeForm?, blockedBy?}] — it replaces the current "
        "list each call. Plan all steps up front in one call."
    )

    def __init__(self) -> None:
        super().__init__()
        self._tasks: list[TaskRecord] = []
        self._next_id: int = 1
        #: Pre-mutation baseline for the call in flight, read by
        #: ``_build_result`` to work out what changed and by ``execute`` to keep
        #: exactly one task running.
        self._before: dict[int, str] = {}
        self._demoted: list[int] = []
        global _current_todo_tool
        _current_todo_tool = self

    @property
    def _todos(self) -> list[TaskRecord]:
        return self._tasks

    @_todos.setter
    def _todos(self, val: list[TaskRecord]) -> None:
        self._tasks = val

    def format_call(self, params: TodoParams) -> str:
        return ""

    @property
    def ui_block(self) -> type | None:
        try:
            from vtx.tui.todo_block import TodoToolBlock

            return TodoToolBlock
        except ImportError:
            return None

    def _enforce_single_in_progress(self, keep_id: int | None = None) -> list[int]:
        """Demote every ``in_progress`` task but ``keep_id`` back to pending.

        The rule that exactly one task is running is what makes the strip's
        single accented row mean anything. Gemini rejects a list with two
        ``in_progress`` entries; rejecting spends a full round trip to tell the
        model something it can be told by fixing it here instead, and the
        model that learns "two in_progress is an error" learns it by burning a
        turn. So the extra rows are demoted and reported back in the result text,
        which the model reads on its next call.
        """
        demoted: list[int] = []
        for task in self._tasks:
            if task.status != "in_progress":
                continue
            if keep_id is not None and task.id == keep_id:
                continue
            task.status = "pending"
            demoted.append(task.id)
        return demoted

    async def execute(
        self, params: TodoParams, cancel_event: asyncio.Event | None = None
    ) -> ToolResult:
        del cancel_event
        self._before = self._snapshot()
        self._demoted = []
        raw_params = params.model_dump(by_alias=True, exclude_none=True)

        # Validate the whole submitted list before mutating anything: bad input
        # must not leave a half-applied plan behind.
        n = len(params.todos)
        records: list[TaskRecord] = []
        error: str | None = None
        for i, item in enumerate(params.todos, start=1):
            content = (item.content or "").strip()
            if not content:
                error = f"item {i}: empty content"
                break
            deps = [int(d) for d in (item.blocked_by or [])]
            bad = [d for d in deps if d < 1 or d > n or d == i]
            if bad:
                error = f"item {i}: blockedBy {bad} out of range (use 1-{n}, not self)"
                break
            records.append(
                TaskRecord(
                    id=i,
                    subject=content,
                    status=item.status,
                    active_form=item.active_form,  # ty:ignore[unknown-argument]
                    blocked_by=deps,  # ty:ignore[unknown-argument]
                )
            )
        if error is None:
            # Cycle check on the submitted graph.
            graph = {t.id: t.blocked_by for t in records}
            for t in records:
                seen: set[int] = set()
                stack = list(t.blocked_by)
                while stack:
                    cur = stack.pop()
                    if cur == t.id:
                        error = f"item {t.id}: blockedBy creates a cycle"
                        break
                    if cur in seen:
                        continue
                    seen.add(cur)
                    stack.extend(graph.get(cur, ()))
                if error:
                    break

        if error is not None:
            return self._build_result(
                action="set", raw_params=raw_params, content_text=f"Error: {error}", error=error
            )

        self._tasks = records
        self._next_id = n + 1

        # Keep the first running task; demote the rest (reported, not rejected).
        first_running = next((t.id for t in records if t.status == "in_progress"), None)
        self._demoted = self._enforce_single_in_progress(keep_id=first_running)

        total = sum(1 for t in records if t.status != "deleted")
        completed = sum(1 for t in records if t.status == "completed")
        note = (
            f" (demoted #{',#'.join(map(str, self._demoted))} to pending; only one task runs "
            f"at a time)"
            if self._demoted
            else ""
        )
        return self._build_result(
            action="set",
            raw_params=raw_params,
            content_text=f"Todos updated: {completed}/{total} completed{note}",
            error=None,
        )

    def _format_task_line(self, task: TaskRecord) -> str:
        active_part = (
            f" ({task.active_form})" if task.status == "in_progress" and task.active_form else ""
        )
        dep_part = f" ⛓ #{','.join(map(str, task.blocked_by))}" if task.blocked_by else ""
        return f"[{task.status}] #{task.id} {task.subject}{active_part}{dep_part}"

    def _snapshot(self) -> dict[int, str]:
        """``{id: status}`` for the live tasks, taken before a mutation.

        The block in the scrollback prints what changed rather than the whole
        list, so it needs to know what "changed" means against the previous
        state. Deltas are keyed by id here because the ids are stable; Crush
        keys by subject text because the model rewrites its whole array each
        call, and this tool is driven by ids instead.
        """
        return {t.id: t.status for t in self._tasks if t.status != "deleted"}

    def _build_result(
        self,
        action: str,
        raw_params: dict[str, Any],
        content_text: str,
        error: str | None,
        before: dict[int, str] | None = None,
    ) -> ToolResult:
        live = [t for t in self._tasks if t.status != "deleted"]
        total = len(live)
        completed = sum(1 for t in live if t.status == "completed")
        before = self._before if before is None else before

        just_completed = [
            t.id for t in live if t.status == "completed" and before.get(t.id) != "completed"
        ]
        just_added = [t.id for t in live if t.id not in before]
        current = next((t for t in live if t.status == "in_progress"), None)
        just_started = (
            current.id if current is not None and before.get(current.id) != "in_progress" else None
        )

        # Ids rather than the strings themselves: the block re-renders the real
        # rows out of ``ui_data``, so it can show a completed task struck
        # through instead of a bare check mark and a subject it has to match up
        # by text.
        delta: dict[str, Any] = {
            "is_new": not before and total > 0,
            "all_completed": total > 0 and completed == total,
            "just_started": just_started,
            "just_completed": just_completed,
            "just_added": just_added,
            "completed": completed,
            "total": total,
        }

        details: dict[str, Any] = {
            "action": action,
            "params": raw_params,
            "tasks": [t.model_dump(by_alias=True, exclude_none=True) for t in self._tasks],
            "nextId": self._next_id,
            "delta": delta,
        }
        if error:
            details["error"] = error

        # Plain text, not markup: these strings reach a session export, where
        # Rich tags would land in the markdown as literal brackets.
        ui_details = format_todo_tree(self._tasks) if error else _format_todo_delta(delta, live)
        ui_details_full = format_todo_tree(self._tasks)

        for cb in list(_todo_refresh_callbacks):
            with contextlib.suppress(Exception):
                cb()

        summary = f"{completed}/{total}" if total > 0 else "0/0"
        if self._demoted:
            details["demoted"] = list(self._demoted)

        return ToolResult(
            success=(error is None),
            result=content_text,
            structured=details,
            ui_summary=summary,
            ui_details=ui_details,
            ui_details_full=ui_details_full,
            ui_data=details,
        )


@plugin(name="tool-todo", inject=("tools",))
def apply(ctx, config):
    mount_tool(ctx, TodoTool(), is_default=True, parent_only=False)


PLUGIN = apply

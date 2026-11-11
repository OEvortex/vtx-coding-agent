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
ActionType = Literal["create", "update", "list", "get", "delete", "clear", "set"]

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"in_progress", "completed", "deleted"},
    "in_progress": {"pending", "completed", "deleted"},
    "completed": {"deleted"},
    "deleted": set(),
}


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


class TodoParams(BaseModel):
    action: ActionType = "create"

    # create-only
    subject: str | None = Field(
        default=None, description="Task subject/title (required for create)"
    )
    blocked_by: list[int] | None = Field(
        default=None, alias="blockedBy", description="Initial dependency task IDs"
    )

    # create + update
    description: str | None = Field(default=None, description="Long-form detail")
    active_form: str | None = Field(
        default=None,
        alias="activeForm",
        description="Present-continuous label shown while in_progress",
    )
    owner: str | None = Field(default=None, description="Agent/owner assigned to this task")
    metadata: dict[str, Any] | None = Field(
        default=None,
        description="Key-value metadata. On update, passing null for a key deletes it.",
    )

    # update-only
    add_blocked_by: list[int] | None = Field(
        default=None, alias="addBlockedBy", description="Additive merge into blockedBy"
    )
    remove_blocked_by: list[int] | None = Field(
        default=None, alias="removeBlockedBy", description="Additive removal from blockedBy"
    )

    # update / get / delete
    id: int | None = Field(default=None, description="Target task ID")

    # update (sets status) or list (filters by status)
    status: StatusType | None = Field(
        default=None, description="Task status ('pending', 'in_progress', 'completed', 'deleted')"
    )

    # list-only
    include_deleted: bool = Field(
        default=False,
        alias="includeDeleted",
        description="Whether to include deleted tasks in list (default false)",
    )

    # batch set / backwards compatibility
    todos: list[Any] | None = Field(default=None, description="Batch tasks to set or initialize")

    model_config = ConfigDict(populate_by_name=True)

    @model_validator(mode="before")
    @classmethod
    def _coerce_params(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        # Normalize subject from alt names
        if "subject" not in data:
            for alt in ("content", "title", "task"):
                if data.get(alt):
                    data["subject"] = data[alt]
                    break

        # If action not explicitly given, infer from presence of fields
        if "action" not in data:
            if "todos" in data and data["todos"] is not None:
                data["action"] = "set"
            elif "id" in data and (
                "status" in data
                or "active_form" in data
                or "activeForm" in data
                or "add_blocked_by" in data
                or "addBlockedBy" in data
            ):
                data["action"] = "update"
            elif "id" in data and "subject" not in data:
                data["action"] = "get"
            elif "subject" in data:
                data["action"] = "create"
            else:
                data["action"] = "list"
        return data


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
        "todo with create|update|list|get|delete|clear actions to track multi-step work",
        "plan the whole task list in ONE call with action='set' and a todos array, then "
        "update individual tasks with action='update' — do not create tasks one call at a time",
        "keep exactly one task in_progress at a time; starting a task demotes the others",
        "always pass activeForm (present continuous, e.g. 'Running the tests') alongside subject "
        "(imperative, e.g. 'Run the tests') so the running task reads as live work",
        "mark tasks completed immediately when done rather than in batches",
        "call todo(action='update', id=N, status='completed') to complete a task",
        "never complete a task if tests are failing — leave it in_progress instead",
        "skip the todo tool for work that is a single trivial step or purely conversational",
    )
    description = (
        "Track tasks with the rpiv-todo schema. Actions: "
        "'set' (batch plan — pass the whole todos array, replaces the list), "
        "'create' (needs subject), "
        "'update' (needs id + field to update), 'list' (returns tasks, filters by status), "
        "'get' (needs id, returns task with dependencies), 'delete' (tombstones by id), "
        "'clear' (resets all tasks). "
        "Status is one of pending | in_progress | completed | deleted; a task can only move "
        "forward, and exactly one task may be in_progress."
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
    def ui_block(self) -> type | None:  # ty: ignore[incompatible-property-override]
        try:
            from vtx.tui.todo_block import TodoToolBlock

            return TodoToolBlock
        except ImportError:
            return None

    def _find_task(self, task_id: int) -> TaskRecord | None:
        for t in self._tasks:
            if t.id == task_id:
                return t
        return None

    def _would_create_cycle(self, task_id: int, dep_id: int) -> bool:
        # Check if task_id is already a dependency of dep_id
        visited: set[int] = set()
        queue: list[int] = [dep_id]
        while queue:
            curr = queue.pop(0)
            if curr == task_id:
                return True
            if curr in visited:
                continue
            visited.add(curr)
            curr_task = self._find_task(curr)
            if curr_task and curr_task.blocked_by:
                for b in curr_task.blocked_by:
                    queue.append(b)
        return False

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
        action = params.action
        raw_params = params.model_dump(by_alias=True, exclude_none=True)
        self._before = self._snapshot()
        self._demoted = []

        # Batch compatibility mode
        if action == "set" or (params.todos is not None and action != "create"):
            self._tasks.clear()
            self._next_id = 1
            for item in params.todos or []:
                if isinstance(item, dict):
                    subj = item.get("subject") or item.get("content") or item.get("title") or ""
                    status_raw = str(item.get("status", "pending")).lower()
                    status: StatusType = (
                        "completed"
                        if status_raw in ("completed", "done")
                        else "in_progress"
                        if status_raw in ("in_progress", "progress", "active")
                        else "deleted"
                        if status_raw == "deleted"
                        else "pending"
                    )
                    active = item.get("active_form") or item.get("activeForm")
                    task = TaskRecord(
                        id=self._next_id,
                        subject=subj,
                        status=status,
                        active_form=active,
                        blocked_by=item.get("blocked_by") or item.get("blockedBy") or [],
                    )
                elif isinstance(item, TaskRecord):
                    task = item
                else:
                    task = TaskRecord(id=self._next_id, subject=str(item))
                self._tasks.append(task)
                self._next_id = max(self._next_id + 1, task.id + 1)

            # A batch may arrive with several tasks marked running. Keep the
            # first: it is the one the model listed first, and in execution
            # order that is the one it meant to start with.
            first_running = next((t.id for t in self._tasks if t.status == "in_progress"), None)
            self._demoted = self._enforce_single_in_progress(keep_id=first_running)

            total = sum(1 for t in self._tasks if t.status != "deleted")
            completed = sum(1 for t in self._tasks if t.status == "completed")
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

        # 1. CREATE
        if action == "create":
            if not params.subject or not params.subject.strip():
                return self._build_result(
                    action=action,
                    raw_params=raw_params,
                    content_text="Error: subject required for create",
                    error="subject required for create",
                )

            # Validate initial dependencies
            blocked_by: list[int] = []
            if params.blocked_by:
                for dep in params.blocked_by:
                    dep_task = self._find_task(dep)
                    if not dep_task:
                        err = f"blockedBy: #{dep} not found"
                        return self._build_result(
                            action=action,
                            raw_params=raw_params,
                            content_text=f"Error: {err}",
                            error=err,
                        )
                    if dep_task.status == "deleted":
                        err = f"blockedBy: #{dep} is deleted"
                        return self._build_result(
                            action=action,
                            raw_params=raw_params,
                            content_text=f"Error: {err}",
                            error=err,
                        )
                    if dep not in blocked_by:
                        blocked_by.append(dep)

            task_id = self._next_id
            self._next_id += 1
            new_task = TaskRecord(
                id=task_id,
                subject=params.subject.strip(),
                description=params.description,
                active_form=params.active_form,
                status="pending",
                blocked_by=blocked_by,
                owner=params.owner,
                metadata=params.metadata,
            )
            self._tasks.append(new_task)
            return self._build_result(
                action=action,
                raw_params=raw_params,
                content_text=f"Created #{task_id}: {new_task.subject} (pending)",
                error=None,
            )

        # 2. UPDATE
        if action == "update":
            if params.id is None:
                err = "id required for update"
                return self._build_result(
                    action=action, raw_params=raw_params, content_text=f"Error: {err}", error=err
                )

            task = self._find_task(params.id)
            if not task:
                err = f"#{params.id} not found"
                return self._build_result(
                    action=action, raw_params=raw_params, content_text=f"Error: {err}", error=err
                )

            has_mutable = any(
                [
                    params.subject is not None,
                    params.description is not None,
                    params.active_form is not None,
                    params.status is not None,
                    params.owner is not None,
                    params.metadata is not None,
                    params.add_blocked_by is not None,
                    params.remove_blocked_by is not None,
                ]
            )
            if not has_mutable:
                err = (
                    "update requires at least one mutable field: subject, description, "
                    "activeForm, status, owner, metadata, addBlockedBy, or removeBlockedBy"
                )
                return self._build_result(
                    action=action, raw_params=raw_params, content_text=f"Error: {err}", error=err
                )

            old_status = task.status
            new_status = params.status if params.status is not None else old_status

            # Status transition validation
            if new_status != old_status:
                allowed = ALLOWED_TRANSITIONS.get(old_status, set())
                if new_status not in allowed:
                    err = f"illegal transition {old_status} → {new_status}"
                    return self._build_result(
                        action=action,
                        raw_params=raw_params,
                        content_text=f"Error: {err}",
                        error=err,
                    )

            # Dependency updates
            new_blocked_by = list(task.blocked_by)
            if params.add_blocked_by:
                for dep in params.add_blocked_by:
                    if dep == task.id:
                        err = f"cannot block #{task.id} on itself"
                        return self._build_result(
                            action=action,
                            raw_params=raw_params,
                            content_text=f"Error: {err}",
                            error=err,
                        )
                    dep_task = self._find_task(dep)
                    if not dep_task:
                        err = f"addBlockedBy: #{dep} not found"
                        return self._build_result(
                            action=action,
                            raw_params=raw_params,
                            content_text=f"Error: {err}",
                            error=err,
                        )
                    if dep_task.status == "deleted":
                        err = f"addBlockedBy: #{dep} is deleted"
                        return self._build_result(
                            action=action,
                            raw_params=raw_params,
                            content_text=f"Error: {err}",
                            error=err,
                        )
                    if self._would_create_cycle(task.id, dep):
                        err = "addBlockedBy would create a cycle in the blockedBy graph"
                        return self._build_result(
                            action=action,
                            raw_params=raw_params,
                            content_text=f"Error: {err}",
                            error=err,
                        )
                    if dep not in new_blocked_by:
                        new_blocked_by.append(dep)

            if params.remove_blocked_by:
                for dep in params.remove_blocked_by:
                    if dep in new_blocked_by:
                        new_blocked_by.remove(dep)

            # Metadata merge
            new_meta = dict(task.metadata or {}) if task.metadata else {}
            if params.metadata is not None:
                for k, v in params.metadata.items():
                    if v is None:
                        new_meta.pop(k, None)
                    else:
                        new_meta[k] = v

            final_meta = new_meta if new_meta else None

            # Detect if anything changed
            changed = (
                (params.subject is not None and params.subject != task.subject)
                or (params.description is not None and params.description != task.description)
                or (params.active_form is not None and params.active_form != task.active_form)
                or (new_status != old_status)
                or (params.owner is not None and params.owner != task.owner)
                or (final_meta != task.metadata)
                or (new_blocked_by != task.blocked_by)
            )

            if not changed:
                return self._build_result(
                    action=action,
                    raw_params=raw_params,
                    content_text=(
                        f"No change: #{task.id} already matches the requested values "
                        f"(status: {task.status})"
                    ),
                    error=None,
                )

            # Apply mutations
            if params.subject is not None:
                task.subject = params.subject
            if params.description is not None:
                task.description = params.description
            if params.active_form is not None:
                task.active_form = params.active_form
            task.status = new_status
            task.blocked_by = new_blocked_by
            if params.owner is not None:
                task.owner = params.owner
            task.metadata = final_meta

            if new_status != old_status:
                content_text = f"Updated #{task.id} ({old_status} → {new_status})"
            else:
                content_text = f"Updated #{task.id}"

            # Starting a task demotes whatever was running. Doing it here rather
            # than rejecting the call means marking task 7 started never costs
            # the model a turn to fix a mistake it made about task 4.
            if new_status == "in_progress":
                self._demoted = self._enforce_single_in_progress(keep_id=task.id)
                if self._demoted:
                    content_text += f" · demoted #{',#'.join(map(str, self._demoted))} to pending"

            return self._build_result(
                action=action, raw_params=raw_params, content_text=content_text, error=None
            )

        # 3. GET
        if action == "get":
            if params.id is None:
                err = "id required for get"
                return self._build_result(
                    action=action, raw_params=raw_params, content_text=f"Error: {err}", error=err
                )

            task = self._find_task(params.id)
            if not task:
                err = f"#{params.id} not found"
                return self._build_result(
                    action=action, raw_params=raw_params, content_text=f"Error: {err}", error=err
                )

            blocks = [
                str(t.id) for t in self._tasks if t.status != "deleted" and task.id in t.blocked_by
            ]
            row = self._format_task_line(task)
            if blocks:
                row += f"\nblocks: #{','.join(blocks)}"

            return self._build_result(
                action=action, raw_params=raw_params, content_text=row, error=None
            )

        # 4. DELETE
        if action == "delete":
            if params.id is None:
                err = "id required for delete"
                return self._build_result(
                    action=action, raw_params=raw_params, content_text=f"Error: {err}", error=err
                )

            task = self._find_task(params.id)
            if not task:
                err = f"#{params.id} not found"
                return self._build_result(
                    action=action, raw_params=raw_params, content_text=f"Error: {err}", error=err
                )
            if task.status == "deleted":
                err = f"#{params.id} is already deleted"
                return self._build_result(
                    action=action, raw_params=raw_params, content_text=f"Error: {err}", error=err
                )

            task.status = "deleted"
            return self._build_result(
                action=action,
                raw_params=raw_params,
                content_text=f"Deleted #{task.id}: {task.subject}",
                error=None,
            )

        # 5. CLEAR
        if action == "clear":
            count = len(self._tasks)
            self._tasks.clear()
            self._next_id = 1
            return self._build_result(
                action=action,
                raw_params=raw_params,
                content_text=f"Cleared {count} tasks",
                error=None,
            )

        # 6. LIST
        tasks_to_show = self._tasks
        if not params.include_deleted:
            tasks_to_show = [t for t in tasks_to_show if t.status != "deleted"]
        if params.status:
            tasks_to_show = [t for t in tasks_to_show if t.status == params.status]

        if not tasks_to_show:
            content_text = "No tasks"
        else:
            content_text = "\n".join(self._format_task_line(t) for t in tasks_to_show)

        return self._build_result(
            action=action, raw_params=raw_params, content_text=content_text, error=None
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

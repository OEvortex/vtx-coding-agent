"""The single LLM-facing goal tool.

One ``goal`` tool with an ``action`` parameter covers the whole lifecycle
(mirrors the ``skill`` tool's action-based design)::

    goal(action="create",      objective=..., mode=..., verification=..., token_budget=...)
    goal(action="get")
    goal(action="update",      status=..., reason=..., completion_summary=..., ...)
    goal(action="set_tasks",   tasks=[{title, id?, parent_id?}, ...])
    goal(action="update_task", task_id=..., task_status=..., evidence=..., ...)

All actions operate on the focused goal (focus is user-owned; no tool can
switch it) through the
:class:`~vtx.coding_agent.goal.service.GoalService` mutation boundary.
"""

from __future__ import annotations

import asyncio
import logging
import os

from pydantic import BaseModel, Field

from vtx.ai.agent.tools.base import BaseTool
from vtx.core.types import ToolResult

from .prompts import status_line
from .record import GOAL_MODES, GoalRecord, count_tasks, current_task, objective_title
from .service import GoalError, GoalService, get_service, goal_progress

log = logging.getLogger("agent.goal.tools")

GOAL_ACTIONS = ("create", "get", "update", "set_tasks", "update_task")


def _cwd() -> str:
    try:
        from vtx.ai.agent.dispatcher import get_context

        ctx = get_context()
        if ctx and ctx.cwd:
            return ctx.cwd
    except Exception:
        pass
    return os.getcwd()


def _service() -> GoalService:
    return get_service(_cwd())


def _require_focused(service: GoalService) -> GoalRecord:
    record = service.focused()
    if record is None:
        raise GoalError("No focused open goal. Create one only after an explicit user request.")
    return record


def _goal_file_hint(cwd: str, goal_id: str) -> str:
    from .storage import find_goal_file

    path = find_goal_file(cwd, goal_id)
    if path is None:
        return f".vtx/goals/active_goal_*_{goal_id}.md"
    try:
        return str(path.relative_to(cwd))
    except ValueError:
        return str(path)


def _snapshot_text(record: GoalRecord, service: GoalService) -> str:
    done, total = count_tasks(record.tasks)
    lines = [
        f"Goal {record.id} ({record.mode})",
        f"Objective: {objective_title(record.objective, 200)}",
        f"Status: {status_line(service, record)}",
        f"Tasks: {done}/{total}",
    ]
    task = current_task(record)
    if task:
        lines.append(f"Current: [{task.id}] {task.title}")
    if record.review_feedback:
        lines.append(f"Auditor feedback: {record.review_feedback}")
    lines.append(f"File: {_goal_file_hint(service.cwd, record.id)}")
    return "\n".join(lines)


def _err(exc: GoalError) -> ToolResult:
    return ToolResult(success=False, result=str(exc), ui_summary=f"[red]{exc}[/red]")


def _short(text: str, limit: int = 300) -> str:
    text = (text or "").strip()
    for marker in ("<approved/>", "<disapproved/>"):
        text = text.replace(marker, "").strip()
    if len(text) > limit:
        return text[: limit - 1].rstrip() + "…"
    return text


class GoalTaskItem(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    id: str | None = Field(default=None, description="Stable id like t1 or t1.1")
    parent_id: str | None = Field(default=None, description="Parent task id for subtasks")
    note: str | None = Field(default="", max_length=500)


class GoalParams(BaseModel):
    action: str = Field(
        description=(
            "Operation to perform: 'create', 'get', 'update', 'set_tasks', or 'update_task'"
        )
    )
    # create
    objective: str | None = Field(
        default=None,
        max_length=4000,
        description="create / update(revise): complete goal objective and outcome to achieve",
    )
    mode: str | None = Field(
        default=None,
        description="create: 'regular' (open-ended) or 'sisyphus' (strict sequential tasks)",
    )
    verification: str | None = Field(
        default=None,
        description="create: completion contract or test command required for verification",
    )
    token_budget: int | None = Field(
        default=None, description="create: optional total-token budget ceiling for the goal"
    )
    # update
    status: str | None = Field(
        default=None,
        description="update: status — 'complete', 'blocked', 'paused', 'active', or 'revise'",
    )
    reason: str | None = Field(
        default=None,
        description="update: explanation required for 'blocked', 'paused', or revision notes",
    )
    completion_summary: str | None = Field(
        default=None,
        description="update (complete): concise summary of completed work submitted for audit",
    )
    review_feedback: str | None = Field(
        default=None, description="update: auditor changes-required feedback to record"
    )
    # set_tasks
    tasks: list[GoalTaskItem] | None = Field(
        default=None,
        description="set_tasks: structured task list replacing the current execution plan",
    )
    # update_task
    task_id: str | None = Field(
        default=None, description="update_task: target task identifier (e.g. 't1')"
    )
    task_status: str | None = Field(
        default=None,
        description="update_task: new status — 'start', 'complete', 'skipped', or 'pending'",
    )
    evidence: str | None = Field(
        default=None,
        description="update_task: test output or verification evidence proving task completion",
    )
    note: str | None = Field(
        default=None,
        max_length=500,
        description="update_task: optional note or explanation for skipping",
    )
    subtasks: list[GoalTaskItem] | None = Field(
        default=None, description="update_task: list of subtasks to attach under the target task"
    )


class GoalTool(BaseTool):
    name = "goal"
    tool_icon = "◈"
    params = GoalParams
    # Lifecycle bookkeeping on a project-local markdown file: safe to run
    # without approval prompts. Destructive archiving requires explicit user
    # confirmation.
    mutating = False
    prompt_guidelines = (
        'goal(action="get") for the focused-goal snapshot; '
        'goal(action="update", status="complete") only when the objective is '
        "genuinely satisfied; create only after an explicit user request or "
        "confirmed proposal",
    )
    description = (
        "Manage persistent, multi-turn project goals and task trees. "
        "Track progress with 'create', 'get', 'set_tasks', 'update_task', or 'update'. "
        "Marking a goal complete triggers an independent auditor verification step."
    )

    def format_call(self, params: GoalParams) -> str:
        detail = ""
        if params.action == "create" and params.objective:
            detail = objective_title(params.objective, 60)
        elif params.action == "update":
            extra = params.reason or params.completion_summary or ""
            detail = (params.status or "") + (f" · {objective_title(extra, 50)}" if extra else "")
        elif params.action == "set_tasks":
            detail = f"{len(params.tasks or [])} tasks"
        elif params.action == "update_task":
            detail = f"{params.task_id} → {params.task_status}"
            extra = params.evidence or params.note or ""
            if extra:
                detail += f" · {objective_title(extra, 40)}"
        return params.action + (f" · {detail}" if detail else "")

    async def execute(
        self, params: GoalParams, cancel_event: asyncio.Event | None = None
    ) -> ToolResult:
        action = (params.action or "").strip().lower()
        if action not in GOAL_ACTIONS:
            return _err(GoalError(f"action must be one of {GOAL_ACTIONS}"))
        handler = {
            "create": self._create,
            "get": self._get,
            "update": self._update,
            "set_tasks": self._set_tasks,
            "update_task": self._update_task,
        }[action]
        return await handler(params, cancel_event)

    # ------------------------------------------------------------------
    # create
    # ------------------------------------------------------------------

    async def _create(self, params: GoalParams, cancel_event: asyncio.Event | None) -> ToolResult:
        del cancel_event
        service = _service()
        if service.settings.get("disabled"):
            return _err(GoalError("The goal system is disabled in settings"))
        mode = params.mode or "regular"
        if mode not in GOAL_MODES:
            return _err(GoalError(f"mode must be one of {GOAL_MODES}"))
        if not (params.objective or "").strip():
            return _err(GoalError("create requires an objective"))
        if service.focused() is not None:
            focused = service.focused()
            title = objective_title(focused.objective, 60) if focused else ""
            return _err(
                GoalError(
                    f"This session already focuses on an open goal: {title!r}. "
                    "Finish it or ask the user to archive/unfocus first."
                )
            )
        try:
            record = service.create(
                params.objective or "",
                mode=mode,
                verification=params.verification or "",
                token_budget=params.token_budget,
                source="goal-tool",
            )
        except (GoalError, ValueError) as exc:
            return _err(exc if isinstance(exc, GoalError) else GoalError(str(exc)))
        text = (
            "Goal created and focused.\n"
            + _snapshot_text(record, service)
            + "\nBegin working toward the objective now."
        )
        return ToolResult(success=True, result=text, ui_summary=objective_title(record.objective))

    # ------------------------------------------------------------------
    # get
    # ------------------------------------------------------------------

    async def _get(self, params: GoalParams, cancel_event: asyncio.Event | None) -> ToolResult:
        del params, cancel_event
        service = _service()
        try:
            record = _require_focused(service)
        except GoalError as exc:
            return _err(exc)
        from .storage import render_tasks_markdown

        parts = [
            _snapshot_text(record, service),
            "",
            f"Full objective:\n{record.objective.strip()}",
        ]
        if record.verification.strip():
            parts.append(f"\nVerification contract:\n{record.verification.strip()}")
        if record.tasks:
            parts.append("\nTasks:\n" + render_tasks_markdown(record))
        return ToolResult(success=True, result="\n".join(parts), ui_summary=record.label())

    # ------------------------------------------------------------------
    # update
    # ------------------------------------------------------------------

    async def _update(self, params: GoalParams, cancel_event: asyncio.Event | None) -> ToolResult:
        service = _service()
        if service.settings.get("disabled"):
            return _err(GoalError("The goal system is disabled in settings"))
        try:
            record = _require_focused(service)
        except GoalError as exc:
            return _err(exc)

        status = (params.status or "").strip().lower()
        summary = (params.completion_summary or "").strip()
        reason = (params.reason or "").strip()

        if status == "complete":
            return await self._complete(service, record, summary, cancel_event)
        if status == "blocked":
            if not reason:
                return _err(GoalError("blocked requires a reason"))
            updated = service.set_status(record.id, "blocked", reason=reason)
            return ToolResult(
                success=True,
                result=(
                    "Goal marked blocked. It stays open; the user can resume it.\n"
                    + _snapshot_text(updated, service)
                ),
                ui_summary=updated.label(),
            )
        if status == "paused":
            updated = service.set_status(record.id, "paused", reason=reason or "paused by agent")
            return ToolResult(
                success=True,
                result="Goal paused.\n" + _snapshot_text(updated, service),
                ui_summary=updated.label(),
            )
        if status == "active":
            updated = service.set_status(
                record.id, "active", review_feedback=params.review_feedback
            )
            return ToolResult(
                success=True,
                result="Goal active (resumed).\n" + _snapshot_text(updated, service),
                ui_summary=updated.label(),
            )
        if status == "revise":
            if not params.objective and not reason:
                return _err(GoalError("revise requires objective= or reason=change notes"))
            updated = service.tweak(
                record.id, reason or params.objective or "revised", new_objective=params.objective
            )
            return ToolResult(
                success=True,
                result="Goal revised.\n" + _snapshot_text(updated, service),
                ui_summary=objective_title(updated.objective),
            )
        return _err(GoalError("status must be complete | blocked | paused | active | revise"))

    async def _complete(
        self,
        service: GoalService,
        record: GoalRecord,
        summary: str,
        cancel_event: asyncio.Event | None,
    ) -> ToolResult:
        settings = service.settings
        done, total, pct = goal_progress(record)

        # Record completion summary if provided so the auditor and archive have it.
        if summary:

            def apply_summary(r: GoalRecord) -> None:
                r.completion_summary = summary[:2000]

            service.mutate(record.id, apply_summary)

        if not settings.get("auditorEnabled", True):
            archived = service.archive(record.id)
            from .storage import append_ledger

            append_ledger(service.cwd, "audit_skipped", archived.id)
            path_hint = _archive_path_text(service, archived)
            text = (
                "Completion recorded without independent audit (auditor disabled). "
                "This completion is NOT independently approved.\n"
                f"Archived: {path_hint}"
            )
            return ToolResult(success=True, result=text, ui_summary="archived (no audit)")

        from vtx.ai.agent.dispatcher import get_context

        ctx = get_context()
        if ctx is None or ctx.provider is None:
            return _err(GoalError("no provider available to run the completion auditor"))

        from .auditor import run_completion_audit

        fresh = service.get(record.id) or record
        audit = await run_completion_audit(
            fresh,
            cwd=service.cwd,
            provider=ctx.provider,
            model=ctx.model,
            model_provider=ctx.model_provider,
            base_url=ctx.base_url,
            thinking_level=ctx.thinking_level,
            cancel_event=cancel_event,
        )
        if cancel_event is not None and cancel_event.is_set():
            service.set_status(record.id, "active")
            return ToolResult(
                success=False,
                result="Audit aborted by user; goal remains open.",
                ui_summary="[yellow]audit aborted[/yellow]",
            )
        if audit.error and not audit.summary:
            service.set_status(record.id, "active")
            return _err(GoalError(f"completion audit failed: {audit.error}"))

        from .storage import append_ledger

        if audit.approved:
            archived = service.archive(record.id)
            append_ledger(
                service.cwd, "audit_approved", archived.id, summary=_short(audit.summary)
            )
            text = (
                "Independent audit APPROVED — goal archived as complete.\n"
                f"{_archive_path_text(service, archived)}\n\n"
                f"Auditor summary:\n{_short(audit.summary, 1200)}"
            )
            return ToolResult(success=True, result=text, ui_summary="audit approved ✓")

        feedback = _short(audit.summary, 1500) or "Requirements not met."
        service.set_status(record.id, "active", review_feedback=feedback)
        append_ledger(service.cwd, "audit_changes_required", record.id, summary=_short(feedback))
        tasks_note = f" Task progress was {done}/{total} ({pct}%)." if total else ""
        text = (
            "Independent audit requires more work — goal stays open with feedback.\n"
            f"{tasks_note}\nAuditor notes:\n{feedback}"
        )
        return ToolResult(success=False, result=text, ui_summary="changes required ✗")

    # ------------------------------------------------------------------
    # set_tasks
    # ------------------------------------------------------------------

    async def _set_tasks(
        self, params: GoalParams, cancel_event: asyncio.Event | None
    ) -> ToolResult:
        del cancel_event
        service = _service()
        if service.settings.get("disableTasks"):
            return _err(GoalError("Task lists are disabled in settings"))
        items = [item.model_dump(exclude_none=True) for item in (params.tasks or [])]
        if not items:
            return _err(GoalError("set_tasks requires a non-empty tasks list"))
        try:
            record = _require_focused(service)
            updated = service.replace_tasks(record.id, items)
        except GoalError as exc:
            return _err(exc)
        done, total = count_tasks(updated.tasks)
        listing = "\n".join(
            f"  [{t.id}]{' (sub)' if t.parent_id else ''} {t.title}" for t in updated.tasks[:40]
        )
        return ToolResult(
            success=True,
            result=(
                f"Task list set: {total} top-level tasks ({done} already complete).\n" + listing
            ),
            ui_summary=f"{done}/{total} done",
        )

    # ------------------------------------------------------------------
    # update_task
    # ------------------------------------------------------------------

    async def _update_task(
        self, params: GoalParams, cancel_event: asyncio.Event | None
    ) -> ToolResult:
        del cancel_event
        service = _service()
        if service.settings.get("disableTasks"):
            return _err(GoalError("Task lists are disabled in settings"))
        task_id = (params.task_id or "").strip()
        task_status = (params.task_status or "").strip().lower()
        if not task_id:
            return _err(GoalError("update_task requires task_id"))
        try:
            record = _require_focused(service)
            updated = service.update_task(
                record.id,
                task_id,
                task_status,
                evidence=params.evidence or "",
                note=params.note or "",
                subtasks=[item.model_dump(exclude_none=True) for item in (params.subtasks or [])]
                or None,
            )
        except GoalError as exc:
            return _err(exc)
        done, total, pct = goal_progress(updated)
        task = next((t for t in updated.tasks if t.id == task_id), None)
        detail = f"[{task_id}] {task.title} → {task_status}" if task else ""
        active = current_task(updated)
        tail = (
            f"\nCurrent task now: [{active.id}] {active.title}"
            if active is not None
            else "\nNo pending tasks remain."
        )
        return ToolResult(
            success=True,
            result=(f"Task updated: {detail}\nProgress: {done}/{total} ({pct}%)" + tail),
            ui_summary=f"{done}/{total} done",
        )


def _archive_path_text(service: GoalService, record: GoalRecord) -> str:
    from .storage import find_goal_file

    path = find_goal_file(service.cwd, record.id)
    return str(path) if path else ".vtx/goals/archived/"

"""The single LLM-facing goal tool."""

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
    if path is not None:
        try:
            return str(path.relative_to(cwd))
        except ValueError:
            return str(path)
    return f".vtx/goals/active_goal_*_{goal_id}.md"


class GoalTaskInput(BaseModel):
    title: str = Field(min_length=1, max_length=300, description="Imperative task description")
    id: str | None = Field(default=None, description="Optional custom id like 't1', 't1.1'")
    parent_id: str | None = Field(default=None, description="Parent task id for nesting")
    note: str | None = Field(default=None, max_length=500, description="Optional notes or hints")


class GoalParams(BaseModel):
    action: str = Field(
        description="One of: 'create', 'get', 'update', 'set_tasks', 'update_task'"
    )
    objective: str | None = Field(
        default=None,
        max_length=4000,
        description="The measurable goal to achieve. Required for create; required for revise.",
    )
    mode: str | None = Field(
        default=None,
        description="'regular' (flexible task order) or 'sisyphus' (strict ordered dependencies)",
    )
    verification: str | None = Field(
        default=None,
        description="Contract explaining how completion will be proven (tests, commands, outputs)",
    )
    token_budget: int | None = Field(
        default=None,
        ge=1000,
        description="Optional token budget ceiling. Warning state triggers when exceeded.",
    )
    tasks: list[GoalTaskInput] | None = Field(
        default=None, description="List of tasks for 'set_tasks' or initial tasks on 'create'"
    )
    status: str | None = Field(
        default=None,
        description=(
            "Target status for 'update'. Options: 'active', 'paused', 'blocked', "
            "'complete', 'revise'"
        ),
    )
    reason: str | None = Field(
        default=None, description="Explanation for status change. Required when status='blocked'."
    )
    completion_summary: str | None = Field(
        default=None,
        description="Final summary when status='complete'. Submitted to the auditor agent.",
    )
    task_id: str | None = Field(
        default=None, description="Target task id for 'update_task' (e.g. 't1', 't1.2')"
    )
    task_status: str | None = Field(
        default=None,
        description="New task status for 'update_task': 'pending', 'complete', or 'skipped'",
    )
    task_title: str | None = Field(
        default=None, max_length=300, description="Optional new title for 'update_task'"
    )
    evidence: str | None = Field(
        default=None,
        description="Concrete proof the task is done (test output, diff summary, file path)",
    )
    note: str | None = Field(
        default=None,
        max_length=500,
        description="Reason for skipping or extra task notes on 'update_task'",
    )


class GoalTool(BaseTool[GoalParams]):
    name = "goal"
    tool_icon = "◆"
    params = GoalParams
    mutating = True

    description = (
        "Track durable, multi-turn goals. All actions operate on the focused goal (focus is "
        "user-owned). Actions: 'create' (objective, mode, verification), 'get', 'update' (status: "
        "'active'|'paused'|'blocked'|'complete'|'revise'), 'set_tasks' (list of tasks), "
        "'update_task' (task_id, task_status: 'pending'|'complete'|'skipped', evidence)."
    )

    prompt_guidelines = (
        "Never create a goal proactively: draft and confirm with the user first.",
        (
            "After creating a goal, always call `goal(action='set_tasks')` to provide a "
            "realistic task breakdown."
        ),
        (
            "Mark tasks complete as you finish them with `goal(action='update_task', "
            "task_id=..., task_status='complete', evidence=...)`."
        ),
        (
            "When the objective is genuinely achieved, call `goal(action='update', "
            "status='complete', completion_summary=...)` to trigger the completion audit."
        ),
        (
            "If you are genuinely blocked after multiple attempts, call `goal(action='update', "
            "status='blocked', reason=...)` instead of pretending you succeeded."
        ),
    )

    def format_call(self, params: GoalParams) -> str:
        act = (params.action or "").strip()
        if act == "create":
            title = objective_title(params.objective or "")
            mode_part = f" [{params.mode}]" if params.mode else ""
            return f"create{mode_part}: {title}"
        if act == "get":
            return "get"
        if act == "update":
            return f"update → {params.status or 'status'}"
        if act == "set_tasks":
            n = len(params.tasks or [])
            return f"set_tasks ({n} item{'s' if n != 1 else ''})"
        if act == "update_task":
            st = f" → {params.task_status}" if params.task_status else ""
            return f"update_task [{params.task_id or '?'}{st}]"
        return act or "goal"

    async def execute(
        self,
        params: GoalParams,
        cancel_event: asyncio.Event | None = None,
        tool_call_id: str | None = None,
    ) -> ToolResult:
        act = (params.action or "").strip()
        if not act or act not in GOAL_ACTIONS:
            return ToolResult(
                success=False,
                result=f"Unknown goal action {act!r}. Must be one of: {', '.join(GOAL_ACTIONS)}",
            )
        service = _service()
        try:
            if act == "create":
                return await self._create(service, params)
            if act == "get":
                return await self._get(service)
            if act == "update":
                return await self._update(service, params, cancel_event=cancel_event)
            if act == "set_tasks":
                return await self._set_tasks(service, params)
            if act == "update_task":
                return await self._update_task(service, params)
        except GoalError as exc:
            return ToolResult(success=False, result=f"Goal error: {exc}")
        except Exception as exc:
            log.exception("GoalTool.%s failed", act)
            return ToolResult(success=False, result=f"Goal operation failed: {exc}")
        return ToolResult(success=False, result="Unhandled action")

    async def _create(self, service: GoalService, params: GoalParams) -> ToolResult:
        if not params.objective or not params.objective.strip():
            raise GoalError("Objective is required to create a goal")
        mode = params.mode or "regular"
        if mode not in GOAL_MODES:
            raise GoalError(f"Unknown mode {mode!r}. Options: {', '.join(GOAL_MODES)}")
        raw_tasks = [t.model_dump() for t in params.tasks] if params.tasks else None
        record = service.create(
            params.objective,
            mode=mode,
            verification=params.verification or "",
            token_budget=params.token_budget,
            tasks=raw_tasks,
            auto_focus=True,
        )
        file_hint = _goal_file_hint(service.cwd, record.id)
        out = [
            f"Created and focused goal {record.id} ({record.mode}):",
            f"  objective: {record.objective.strip()}",
            f"  status: {status_line(service, record)}",
            f"  file: {file_hint}",
        ]
        if record.verification:
            out.append(f"  verification: {record.verification}")
        if record.tasks:
            done, total = count_tasks(record.tasks)
            out.append(f"  tasks: {done}/{total} complete ({len(record.tasks)} total nodes)")
        return ToolResult(
            success=True,
            result="\n".join(out),
            ui_summary=f"created goal {record.id}",
            ui_details=f"Focused: {objective_title(record.objective)}",
        )

    async def _get(self, service: GoalService) -> ToolResult:
        record = service.focused()
        if record is None:
            pool = service.pool()
            if not pool:
                return ToolResult(
                    success=True,
                    result="No open goals in this project.",
                    ui_summary="no open goals",
                )
            rows = [
                f"- [{r.id}] ({r.mode}, {r.label()}): {objective_title(r.objective)}"
                for r in pool.values()
            ]
            return ToolResult(
                success=True,
                result="No goal is currently focused. Open goals in this project:\n"
                + "\n".join(rows),
                ui_summary=f"{len(pool)} open goal{'s' if len(pool) != 1 else ''} (unfocused)",
            )
        from .storage import render_tasks_markdown

        done, total, pct = goal_progress(record)
        lines = [
            f"Focused goal {record.id} ({record.mode}):",
            f"  status: {status_line(service, record)}",
            f"  objective: {record.objective.strip()}",
        ]
        if record.verification:
            lines.append(f"  verification: {record.verification}")
        if record.blocked_reason:
            lines.append(f"  blocked_reason: {record.blocked_reason}")
        if record.review_feedback:
            lines.append(f"  review_feedback: {record.review_feedback}")
        lines.append(f"  progress: {done}/{total} top-level tasks ({pct}%)")
        if record.tasks:
            lines.append("\nTasks:\n" + render_tasks_markdown(record))
        return ToolResult(
            success=True,
            result="\n".join(lines),
            ui_summary=f"goal {record.id} ({done}/{total} {pct}%)",
        )

    async def _update(
        self, service: GoalService, params: GoalParams, cancel_event: asyncio.Event | None = None
    ) -> ToolResult:
        record = _require_focused(service)
        target = (params.status or "").strip().lower()
        if not target:
            raise GoalError("status is required for update")
        if target == "revise":
            if not params.objective or not params.objective.strip():
                raise GoalError("objective is required when status='revise'")
            updated = service.tweak(
                record.id,
                objective=params.objective,
                verification=params.verification,
                token_budget=params.token_budget,
                change_summary=params.reason or "Revised via tool",
            )
            return ToolResult(
                success=True,
                result=f"Goal {updated.id} revised. New objective:\n{updated.objective}",
                ui_summary="revised objective",
            )

        if target == "complete":
            return await self._handle_complete(service, record, params, cancel_event=cancel_event)

        if target in ("active", "paused", "blocked"):
            updated = service.update_status(
                record.id,
                target,
                reason=params.reason,
                completion_summary=params.completion_summary,
            )
            return ToolResult(
                success=True,
                result=(
                    f"Goal {updated.id} status updated to {updated.status} "
                    f"({status_line(service, updated)})."
                ),
                ui_summary=f"status → {updated.status}",
            )
        raise GoalError(
            f"Unsupported update status {target!r}. Use: active, paused, blocked, complete, revise"
        )

    async def _handle_complete(
        self,
        service: GoalService,
        record: GoalRecord,
        params: GoalParams,
        cancel_event: asyncio.Event | None = None,
    ) -> ToolResult:
        settings = service.settings
        summary = params.completion_summary or params.reason or "Completed"

        if not settings.get("auditorEnabled", True):
            service.record_audit_verdict(record.id, approved=True, summary=summary)
            return ToolResult(
                success=True,
                result=f"Goal {record.id} marked complete and archived (auditor disabled).",
                ui_summary="completed (no audit)",
            )

        from vtx.ai.agent.dispatcher import get_context

        ctx = get_context()
        if ctx is None:
            raise GoalError("No dispatcher context available to run the completion audit")

        # Save completion summary claim onto the record first
        staged = service.tweak(record.id, change_summary="Staging completion claim for audit")
        staged.completion_summary = summary

        from .auditor import run_completion_audit

        audit = await run_completion_audit(
            staged,
            cwd=service.cwd,
            provider=ctx.provider,
            model=ctx.model,
            model_provider=ctx.model_provider,
            base_url=ctx.base_url,
            thinking_level=ctx.thinking_level,
            cancel_event=cancel_event,
        )

        if audit.approved:
            archived = service.record_audit_verdict(
                record.id, approved=True, summary=audit.summary or summary
            )
            return ToolResult(
                success=True,
                result=(
                    f"Goal {archived.id} passed completion audit and was archived.\n"
                    f"Auditor summary: {audit.summary}\n"
                    f"File moved to .vtx/goals/archived/"
                ),
                ui_summary="completed (audit approved)",
            )

        updated = service.record_audit_verdict(
            record.id, approved=False, feedback=audit.summary, summary=summary
        )
        return ToolResult(
            success=False,
            result=(
                f"Completion audit did NOT approve goal {updated.id}.\n"
                f"Auditor feedback:\n{audit.summary}\n\n"
                "The goal remains open. Address the feedback and request completion again."
            ),
            ui_summary="audit changes required",
        )

    async def _set_tasks(self, service: GoalService, params: GoalParams) -> ToolResult:
        record = _require_focused(service)
        if params.tasks is None:
            raise GoalError("tasks parameter is required for set_tasks")
        raw = [t.model_dump() for t in params.tasks]
        updated = service.set_tasks(record.id, raw)
        done, total, pct = goal_progress(updated)
        cur = current_task(updated)
        cur_text = f"Current task: [{cur.id}] {cur.title}" if cur else "All tasks complete."
        return ToolResult(
            success=True,
            result=(
                f"Task list updated for goal {updated.id}: {done}/{total} complete ({pct}%).\n"
                f"{cur_text}"
            ),
            ui_summary=f"set {len(updated.tasks)} tasks",
        )

    async def _update_task(self, service: GoalService, params: GoalParams) -> ToolResult:
        record = _require_focused(service)
        if not params.task_id:
            raise GoalError("task_id is required for update_task")
        updated, task = service.update_task(
            record.id,
            params.task_id,
            status=params.task_status,
            title=params.task_title,
            evidence=params.evidence,
            note=params.note,
        )
        done, total, pct = goal_progress(updated)
        cur = current_task(updated)
        next_hint = f"\nNext task: [{cur.id}] {cur.title}" if cur and cur.id != task.id else ""
        return ToolResult(
            success=True,
            result=(
                f"Task [{task.id}] updated ({task.status}). "
                f"Goal progress: {done}/{total} ({pct}%).{next_hint}"
            ),
            ui_summary=f"task {task.id} → {task.status}",
        )


__all__ = ["GOAL_ACTIONS", "GoalParams", "GoalTaskInput", "GoalTool"]

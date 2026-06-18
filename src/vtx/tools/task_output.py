"""Companion retrieval tool for background sub-agents dispatched by
:class:`~vtx.tools.task.TaskTool` with ``background: true``.

When the Task tool is invoked with ``background: true`` the sub-agent
runs concurrently and the call returns immediately with a
``task_id``. The parent must use this tool to wait for the result —
it must NOT poll, sleep, or busy-wait.

Mirrors Claude Code's TaskOutput tool.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC

from pydantic import BaseModel, Field

from ..async_utils import OperationCancelledError
from ..core.types import ToolResult
from .background import BackgroundTaskManager, BackgroundTaskRecord, get_manager
from .base import BaseTool

log = logging.getLogger("vtx.tools.task_output")

MAX_RESULT_CHARS = 32_000


class TaskOutputParams(BaseModel):
    task_id: str = Field(
        min_length=1,
        max_length=128,
        description=(
            "ID returned by a previous background Task call. The Task tool "
            "returns this in its result text when called with background=true."
        ),
    )
    block: bool = Field(
        default=True,
        description=(
            "If true, block up to ``timeout`` seconds for the result. "
            "If false, return immediately with the current status."
        ),
    )
    timeout: float = Field(
        default=300.0,
        ge=0.0,
        le=3600.0,
        description=(
            "Maximum seconds to wait when ``block=true``. Use a short "
            "value if you only want to check progress."
        ),
    )


class TaskOutputTool(BaseTool[TaskOutputParams]):
    name = "task_output"
    params = TaskOutputParams
    tool_icon = "↪"
    mutating = False

    description = (
        "Wait for, or check the status of, a background sub-agent "
        "dispatched by the Task tool with ``background: true``. "
        "Pass ``block=true`` (the default) to wait up to ``timeout`` "
        "seconds for the sub-agent to finish; the returned text is "
        "the sub-agent's final answer, same shape as a foreground "
        "Task call. Pass ``block=false`` to poll current status "
        "without waiting. The TaskOutput tool is the ONLY retrieval "
        "path for background tasks — do NOT poll or sleep."
    )

    prompt_guidelines = (
        "Use TaskOutput after launching a background Task to retrieve "
        "its result. Default behaviour blocks until completion (or "
        "``timeout``). Pass ``block=false`` to peek at status without "
        "waiting. The returned text is the sub-agent's final answer — "
        "treat it as the authoritative result. Do not chain sleep-and-"
        "poll loops; TaskOutput's block parameter is the wait primitive."
    )

    def format_call(self, params: TaskOutputParams) -> str:
        suffix = "" if params.block else " (non-blocking)"
        return f"{params.task_id}{suffix}"

    async def execute(
        self, params: TaskOutputParams, cancel_event: asyncio.Event | None = None
    ) -> ToolResult:
        manager = get_manager()
        if manager is None:
            return ToolResult(
                success=False,
                result=(
                    "TaskOutput invoked but no BackgroundTaskManager is "
                    "installed. Background tasks are only available "
                    "inside a running ConversationRuntime."
                ),
            )

        record = manager.get(params.task_id)
        if record is None:
            return ToolResult(
                success=False,
                result=(
                    f"Unknown task_id {params.task_id!r}. The task may "
                    f"have been discarded. Use the /tasks command to "
                    f"list known background tasks."
                ),
            )

        if record.status == "running" and not params.block:
            return _still_running_result(record)

        if record.status == "running":
            return await _wait_for_result(manager, record, params, cancel_event)

        # Already finished — ack the notification and return the result.
        record.notified = True
        return _completed_result(record)

    def ui_details_full(self, params: TaskOutputParams, result: ToolResult) -> str | None:
        return result.ui_details_full


def _still_running_result(record: BackgroundTaskRecord) -> ToolResult:
    elapsed = ""
    if record.started_at is not None:
        from datetime import datetime

        delta = datetime.now(UTC) - record.started_at
        seconds = int(delta.total_seconds())
        if seconds < 60:
            elapsed = f"{seconds}s"
        elif seconds < 3600:
            elapsed = f"{seconds // 60}m{seconds % 60}s"
        else:
            elapsed = f"{seconds // 3600}h{(seconds % 3600) // 60}m"
    msg = (
        f"Task {record.short_id()} ({record.description}) is still running"
        + (f" after {elapsed}" if elapsed else "")
        + ". Use block=true with a timeout to wait, or call again later."
    )
    return ToolResult(
        success=True,
        result=msg,
        ui_summary=f"running {record.short_id()}",
        ui_details=None,
        ui_details_full=(
            f"task_id: {record.task_id}\nstatus: running\nstarted: {elapsed or 'just now'}"
        ),
    )


async def _wait_for_result(
    manager: BackgroundTaskManager,
    record: BackgroundTaskRecord,
    params: TaskOutputParams,
    cancel_event: asyncio.Event | None,
) -> ToolResult:
    try:
        finished = await manager.wait(
            params.task_id, timeout=params.timeout, cancel_event=cancel_event
        )
    except TimeoutError:
        return ToolResult(
            success=True,
            result=(
                f"Task {record.short_id()} did not finish within "
                f"{params.timeout:g}s. It is still running. Call again "
                f"with block=true to keep waiting, or block=false to "
                f"peek at status."
            ),
            ui_summary=f"timeout {record.short_id()}",
            ui_details=None,
            ui_details_full=(
                f"task_id: {record.task_id}\nstatus: running\ntimeout: {params.timeout:g}s"
            ),
        )
    except OperationCancelledError:
        return ToolResult(
            success=False,
            result=(
                f"TaskOutput was cancelled while waiting for "
                f"{record.short_id()}. The background task is still "
                f"running — call TaskOutput again to retrieve the "
                f"result later."
            ),
            ui_summary=f"cancelled {record.short_id()}",
            ui_details=None,
            ui_details_full=f"task_id: {record.task_id}\nstatus: running\nnote: caller cancelled",
        )
    except KeyError:
        return ToolResult(success=False, result=f"Unknown task_id {params.task_id!r}.")

    finished.notified = True
    return _completed_result(finished)


def _completed_result(record: BackgroundTaskRecord) -> ToolResult:
    text = record.result_text or "(sub-agent returned no text)"
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS]

    success = record.status == "completed" and record.error is None
    summary = f"{record.short_id()} {record.status}"
    if record.turns:
        summary += f", {record.turns} turn{'s' if record.turns != 1 else ''}"

    details = (
        f"task_id: {record.task_id}\n"
        f"description: {record.description}\n"
        f"subagent_type: {record.subagent_type}\n"
        f"status: {record.status}\n"
        f"turns: {record.turns}\n"
        f"tokens: {record.total_tokens}\n"
    )
    if record.error:
        details += f"error: {record.error}\n"

    return ToolResult(
        success=success, result=text, ui_summary=summary, ui_details=None, ui_details_full=details
    )


__all__ = ["TaskOutputParams", "TaskOutputTool"]

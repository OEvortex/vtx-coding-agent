"""The ``delegate_subagent`` tool.

The dispatch machinery (spec resolution, prompting, sessions, scheduling,
background admission) lives in the harness at :mod:`ctx.agent.subagent`; this
module is the product-facing tool that exposes it to the model.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from ctx.agent.dispatcher import get_context
from ctx.agent.subagent import resolve_subagent_runner, resolve_subagent_spec
from ctx.agent.tools.base import BaseTool
from ctx.plugins import mount_tool, plugin
from ctx.protocol.types import ToolResult
from pydantic import BaseModel, Field

MAX_RESULT_CHARS = 32_000
MAX_TRANSCRIPT_LINES = 200


class TaskParams(BaseModel):
    description: str = Field(
        min_length=1,
        max_length=128,
        description="Concise 3-5 word imperative label (e.g. 'Find the auth bug')",
    )
    prompt: str = Field(
        min_length=1,
        description="Detailed task instructions and context (sub-agent cannot see this chat)",
    )
    subagent_type: str = Field(
        default="",
        description=(
            "Optional name of an agent from .ctx/agent/<name>.py to run as the "
            "sub-agent (e.g. a read-only researcher). Unknown or empty names run "
            "the default sub-agent with the parent's tool surface."
        ),
    )
    model: str | None = Field(
        default=None, description="Optional model override (defaults to parent agent model)"
    )
    background: bool = Field(
        default=False,
        description=(
            "Run concurrently in the background and return a task_id immediately. "
            "Completion arrives next turn; do NOT busy-wait or poll. You can pause and "
            "wait for completion."
        ),
    )


def _format_tokens(n: int) -> str:
    if n < 1000:
        return f"{n}t"
    if n < 10_000:
        return f"{n / 1000:.1f}k"
    return f"{round(n / 1000)}k"


def _format_transcript(transcript: list[str], header: str | None = None) -> str:
    lines: list[str] = []
    if header:
        lines.append(header)
    if not transcript:
        lines.append("(no tool calls)")
        return "\n".join(lines)
    if len(transcript) > MAX_TRANSCRIPT_LINES:
        shown = transcript[:MAX_TRANSCRIPT_LINES]
        lines.extend(shown)
        lines.append(f"... ({len(transcript) - MAX_TRANSCRIPT_LINES} more)")
    else:
        lines.extend(transcript)
    return "\n".join(lines)


class TaskTool(BaseTool[TaskParams]):
    # Renamed from `task` to `delegate_subagent`, which says what the tool
    # actually does.
    name = "delegate_subagent"
    params = TaskParams
    tool_icon = "▸"
    mutating = False

    @property
    def ui_block(self) -> type | None:
        try:
            from ctx.tui.blocks import TaskToolBlock  # ty: ignore[import-not-found]
        except ImportError:
            return None

        return TaskToolBlock

    description = (
        "Dispatch an isolated sub-agent (own tools/session) for a self-contained task; "
        "returns its final text. background: true returns a task_id immediately."
    )

    prompt_guidelines = ()

    def format_call(self, params: TaskParams) -> str:
        return params.description

    async def execute(
        self,
        params: TaskParams,
        cancel_event: asyncio.Event | None = None,
        tool_call_id: str | None = None,
    ) -> ToolResult:
        parent_ctx = get_context()
        if parent_ctx is None:
            return ToolResult(
                success=False,
                result=(
                    "Subagent dispatch invoked before the parent context was "
                    "initialized. This usually means the TUI/headless "
                    "runtime never set the dispatcher context; please "
                    "report it as a bug."
                ),
            )

        if params.background:
            return await self._execute_background(params, parent_ctx, tool_call_id=tool_call_id)

        # Use the parent tool_call_id so progress events key to the same
        # block the TUI created for this tool invocation. Fall back to a
        # generated id for headless/test callers that don't pass one.
        tool_call_id = tool_call_id or f"task_{uuid.uuid4().hex[:12]}"

        spec = resolve_subagent_spec(params.subagent_type, parent_ctx.agent_registry)
        progress_cb = parent_ctx.progress_callback

        runner = resolve_subagent_runner()

        sub_result = await runner(
            parent_ctx=parent_ctx,
            spec=spec,
            prompt=params.prompt,
            cancel_event=cancel_event,
            model_override=params.model,
            progress_callback=progress_cb,
            tool_call_id=tool_call_id,
            label=params.description,
        )

        if sub_result.error is not None and not sub_result.final_text:
            return ToolResult(
                success=False,
                result=f"Sub-agent '{spec.name}' failed: {sub_result.error}",
                ui_summary=f"error: {sub_result.error[:60]}",
                ui_details=None,
                ui_details_full=_format_transcript(sub_result.transcript),
            )

        text = sub_result.final_text or "(sub-agent returned no text)"
        if len(text) > MAX_RESULT_CHARS:
            text = text[:MAX_RESULT_CHARS]

        ui_summary = (
            f"{sub_result.turns} turn{'s' if sub_result.turns != 1 else ''}"
            f" · {_format_tokens(sub_result.usage.total_tokens)}"
        )
        if sub_result.duration_ms is not None:
            ui_summary += f" · {sub_result.duration_ms / 1000:.1f}s"
        if sub_result.error is not None:
            ui_summary += f" · error: {sub_result.error[:40]}"

        ui_details = _format_transcript(
            sub_result.transcript,
            header=(
                f"sub-agent: {spec.name}"
                f" (session {(sub_result.session_id or 'unknown')[:8]})\n"
                f"model: {params.model or spec.model or parent_ctx.model}\n"
                f"result: {sub_result.stop_reason.value}, "
                f"{sub_result.turns} turn(s), "
                f"{_format_tokens(sub_result.usage.total_tokens)} tokens"
            ),
        )

        return ToolResult(
            success=sub_result.error is None,
            result=text,
            ui_summary=ui_summary,
            ui_details=None,
            ui_details_full=ui_details,
        )

    async def _execute_background(
        self, params: TaskParams, parent_ctx: Any, tool_call_id: str | None = None
    ) -> ToolResult:
        """Dispatch a sub-agent and return immediately with a ``task_id``.

        The sub-agent runs concurrently on the asyncio loop; the
        parent turn is unblocked. Completion is reported via a
        :class:`~ctx.events.BackgroundTaskCompletedEvent` yielded
        between parent turns by :meth:`ctx.loop.Agent.run`, and the
        final answer is delivered automatically via a synthetic message.

        Cancellation contract: a background task is not cancelled
        when the parent's ``cancel_event`` fires (the call has
        already returned). It is only cancelled by an explicit
        stop or by :meth:`ctx.runtime.ConversationRuntime.close`.
        """
        from ctx.agent.background import get_manager

        manager = parent_ctx.background_manager or get_manager()
        if manager is None:
            return ToolResult(
                success=False,
                result=(
                    "Background Task requested but no BackgroundTaskManager "
                    "is installed. The headless/runtime must call "
                    "ConversationRuntime.ensure_background_manager() "
                    "before dispatching background sub-agents."
                ),
            )

        spec = resolve_subagent_spec(params.subagent_type, parent_ctx.agent_registry)
        tool_call_id = tool_call_id or f"task_{uuid.uuid4().hex[:12]}"
        progress_cb = parent_ctx.progress_callback
        parent_session_id = getattr(parent_ctx, "session_id", None)
        if parent_session_id is None and getattr(parent_ctx, "session", None) is not None:
            parent_session_id = parent_ctx.session.id

        async def _factory() -> Any:
            runner = resolve_subagent_runner()

            return await runner(
                parent_ctx=parent_ctx,
                spec=spec,
                prompt=params.prompt,
                cancel_event=None,
                model_override=params.model,
                progress_callback=progress_cb,
                tool_call_id=tool_call_id,
                label=params.description,
            )

        record = await manager.register(
            description=params.description,
            prompt=params.prompt,
            subagent_type=spec.name,
            model=params.model or spec.model or parent_ctx.model,
            parent_session_id=parent_session_id,
            run_coro_factory=_factory,
        )

        text = (
            f"Background task launched.\n"
            f"  task_id: {record.task_id}\n"
            f"  description: {record.description}\n"
            f"  subagent_type: {spec.name}\n"
            f"The sub-agent runs concurrently and the final answer will be "
            f"delivered automatically in the next turn."
        )

        return ToolResult(
            success=True,
            result=text,
            ui_summary=f"launched background task {record.short_id()}",
            ui_details=(
                f"⎿  Running in background (ID: {record.task_id})\n"
                f"   {spec.name} · {params.model or spec.model or parent_ctx.model}"
            ),
            ui_details_full=(
                f"task_id: {record.task_id}\n"
                f"description: {record.description}\n"
                f"subagent_type: {spec.name}\n"
                f"model: {params.model or spec.model or parent_ctx.model}\n"
                f"record: {manager._store_dir}/{record.task_id}.json"
            ),
        )


@plugin(name="tool-subagent", inject=("tools",))
def apply(ctx, config):
    mount_tool(ctx, TaskTool(), is_default=True, parent_only=True)


PLUGIN = apply

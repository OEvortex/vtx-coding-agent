"""The ``delegate_subagent`` tool: one isolated agent, for a self-contained task.

The dispatch machinery (spec resolution, prompting, sessions, scheduling,
background admission) lives in the harness at :mod:`ctx.agent.subagent`; this is
vx's tool that exposes it to the model. For several agents working together on
one task, use the swarm tools instead — a sub-agent reports once and is gone.
"""

from __future__ import annotations

import asyncio
import uuid

from ctx.agent.dispatcher import get_context
from ctx.agent.subagent import resolve_subagent_runner, resolve_subagent_spec
from ctx.agent.tools.base import BaseTool
from ctx.protocol.types import ToolResult
from pydantic import BaseModel, Field

MAX_RESULT_CHARS = 32_000
MAX_TRANSCRIPT_LINES = 120


class SubagentParams(BaseModel):
    description: str = Field(
        min_length=1,
        max_length=128,
        description="Concise 3-5 word imperative label, e.g. 'Find the auth bug'",
    )
    prompt: str = Field(
        min_length=1,
        description=(
            "The whole task and the context it needs. The sub-agent cannot see this chat, "
            "so state what to look at and what a good answer looks like."
        ),
    )
    subagent_type: str = Field(
        default="",
        description=(
            "Optional agent name from .ctx/agent/<name>.py (e.g. a read-only researcher). "
            "Empty runs the default sub-agent with your tools minus parent-only ones."
        ),
    )
    model: str | None = Field(
        default=None, description="Optional model override (defaults to this conversation's)"
    )
    background: bool = Field(
        default=False,
        description=(
            "Run concurrently and return a task_id now; the answer arrives automatically in "
            "a later turn. Do not poll it."
        ),
    )


def _format_tokens(n: int) -> str:
    if n < 1000:
        return f"{n}t"
    if n < 10_000:
        return f"{n / 1000:.1f}k"
    return f"{round(n / 1000)}k"


def _format_transcript(transcript: list[str], header: str | None = None) -> str:
    lines: list[str] = [header] if header else []
    if not transcript:
        lines.append("(no tool calls)")
        return "\n".join(lines)
    if len(transcript) > MAX_TRANSCRIPT_LINES:
        lines.extend(transcript[:MAX_TRANSCRIPT_LINES])
        lines.append(f"... ({len(transcript) - MAX_TRANSCRIPT_LINES} more)")
    else:
        lines.extend(transcript)
    return "\n".join(lines)


class SubagentTool(BaseTool[SubagentParams]):
    name = "delegate_subagent"
    params = SubagentParams
    tool_icon = "▸"
    mutating = False
    description = (
        "Dispatch one isolated sub-agent (own session and tools) for a self-contained side "
        "task and get its final text back. It cannot see this conversation, so the prompt "
        "must stand alone. Use `swarm` instead when several agents must work together."
    )

    def format_call(self, params: SubagentParams) -> str:
        return params.description

    async def execute(
        self,
        params: SubagentParams,
        cancel_event: asyncio.Event | None = None,
        tool_call_id: str | None = None,
    ) -> ToolResult:
        parent_ctx = get_context()
        if parent_ctx is None:
            return ToolResult(
                success=False,
                result=(
                    "Sub-agent dispatch ran before the conversation context existed "
                    "(the runtime never installed the dispatcher context)."
                ),
            )

        if params.background:
            return await self._execute_background(params, parent_ctx, tool_call_id)

        tool_call_id = tool_call_id or f"sub_{uuid.uuid4().hex[:12]}"
        spec = resolve_subagent_spec(params.subagent_type, parent_ctx.agent_registry)
        result = await resolve_subagent_runner()(
            parent_ctx=parent_ctx,
            spec=spec,
            prompt=params.prompt,
            cancel_event=cancel_event,
            model_override=params.model,
            progress_callback=parent_ctx.progress_callback,
            tool_call_id=tool_call_id,
            label=params.description,
        )

        if result.error is not None and not result.final_text:
            return ToolResult(
                success=False,
                result=f"Sub-agent '{spec.name}' failed: {result.error}",
                ui_summary=f"error: {result.error[:60]}",
                ui_details_full=_format_transcript(result.transcript),
            )

        text = (result.final_text or "(sub-agent returned no text)")[:MAX_RESULT_CHARS]
        summary = (
            f"{result.turns} turn{'s' if result.turns != 1 else ''}"
            f" · {_format_tokens(result.usage.total_tokens)}"
        )
        if result.duration_ms is not None:
            summary += f" · {result.duration_ms / 1000:.1f}s"
        if result.error is not None:
            summary += f" · error: {result.error[:40]}"

        return ToolResult(
            success=result.error is None,
            result=text,
            ui_summary=summary,
            ui_details_full=_format_transcript(
                result.transcript,
                header=(
                    f"sub-agent: {spec.name}"
                    f" (session {(result.session_id or 'unknown')[:8]})\n"
                    f"model: {params.model or spec.model or parent_ctx.model}\n"
                    f"result: {result.stop_reason.value}, {result.turns} turn(s), "
                    f"{_format_tokens(result.usage.total_tokens)} tokens"
                ),
            ),
        )

    async def _execute_background(
        self, params: SubagentParams, parent_ctx: object, tool_call_id: str | None
    ) -> ToolResult:
        from ctx.agent.background import get_manager

        manager = getattr(parent_ctx, "background_manager", None) or get_manager()
        if manager is None:
            return ToolResult(
                success=False,
                result=(
                    "background=true needs the runtime's background task manager, which is "
                    "not installed in this run. Run it in the foreground instead."
                ),
            )

        spec = resolve_subagent_spec(
            params.subagent_type, getattr(parent_ctx, "agent_registry", None)
        )
        tool_call_id = tool_call_id or f"sub_{uuid.uuid4().hex[:12]}"
        parent_session = getattr(parent_ctx, "session", None)
        parent_session_id = getattr(parent_session, "id", None)

        async def _factory() -> object:
            return await resolve_subagent_runner()(
                parent_ctx=parent_ctx,
                spec=spec,
                prompt=params.prompt,
                cancel_event=None,
                model_override=params.model,
                progress_callback=getattr(parent_ctx, "progress_callback", None),
                tool_call_id=tool_call_id,
                label=params.description,
            )

        record = await manager.register(
            description=params.description,
            prompt=params.prompt,
            subagent_type=spec.name,
            model=params.model or spec.model or getattr(parent_ctx, "model", ""),
            parent_session_id=parent_session_id,
            run_coro_factory=_factory,
        )
        return ToolResult(
            success=True,
            result=(
                f"Background sub-agent launched.\n"
                f"  task_id: {record.task_id}\n"
                f"  description: {params.description}\n"
                f"Its answer arrives automatically in a later turn; do not poll."
            ),
            ui_summary=f"launched background task {record.short_id()}",
            ui_details=f"⎿  Running in background (ID: {record.task_id})",
        )


__all__ = ["SubagentParams", "SubagentTool"]

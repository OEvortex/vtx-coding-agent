from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from vtx import config as vtx_config
from vtx.core.types import StopReason, TextContent, ToolResult, Usage
from vtx.dispatcher import DispatcherContext, get_context

from .base import BaseTool

if TYPE_CHECKING:
    from vtx.llm.base import BaseProvider  # noqa: F401
    from vtx.session import Session

log = logging.getLogger("vtx.tools.task")

MAX_RESULT_CHARS = 32_000
MAX_TRANSCRIPT_LINES = 200


class TaskParams(BaseModel):
    description: str = Field(
        min_length=1,
        max_length=128,
        description="3-5 word imperative label, e.g. 'Find the auth bug'",
    )
    prompt: str = Field(
        min_length=1,
        description="Full instructions incl. all context (sub-agent can't see this conversation)",
    )
    subagent_type: str = Field(
        default="general-purpose",
        description="Preset (general-purpose, Explore, Plan) or user agent name",
    )
    model: str | None = Field(default=None, description="Model override (default: parent's)")
    background: bool = Field(
        default=False,
        description=(
            "Run concurrently, return a task_id now; answer arrives next turn. Don't poll."
        ),
    )


@dataclass
class SubagentSpec:
    """Fully-resolved sub-agent profile, ready to run."""

    name: str
    description: str
    instructions: str | None = None
    instructions_mode: str = "append"  # "append" | "replace"
    tools_allow: list[str] | None = None
    tools_deny: list[str] = field(default_factory=list)
    model: str | None = None
    thinking_level: str | None = None
    max_turns: int | None = None

    def to_agentdef(self) -> Any:
        """Lift the spec into an :class:`AgentDef` for the tool composer."""
        from typing import cast

        from vtx.agents.schema import AgentDef, InstructionsMode, ThinkingLevel

        safe_name = self.name.lower().replace(" ", "-")
        return AgentDef(
            name=safe_name,
            description=self.description,
            instructions=self.instructions,
            instructions_mode=cast(InstructionsMode, self.instructions_mode),
            tools_allow=list(self.tools_allow) if self.tools_allow is not None else None,
            tools_deny=list(self.tools_deny),
            model=self.model,
            thinking_level=cast(ThinkingLevel, self.thinking_level),
            max_turns=self.max_turns,
        )


def _resolve_subagent_spec(subagent_type: str, registry: Any) -> SubagentSpec:
    """Resolve ``subagent_type`` to a concrete :class:`SubagentSpec`."""
    cleaned = (subagent_type or "").strip() or "general-purpose"

    if registry is not None:
        loaded = registry.by_name(cleaned)
        if loaded is not None:
            d = loaded.definition
            return SubagentSpec(
                name=d.name,
                description=d.description,
                instructions=d.instructions,
                instructions_mode=d.instructions_mode,
                tools_allow=list(d.tools_allow) if d.tools_allow is not None else None,
                tools_deny=list(d.tools_deny),
                model=d.model,
                thinking_level=d.thinking_level,
                max_turns=d.max_turns,
            )

    presets = {p.name: p for p in vtx_config.task.subagent_presets}
    preset = presets.get(cleaned)
    if preset is not None:
        return _spec_from_preset(preset)

    if "general-purpose" in presets:
        return _spec_from_preset(presets["general-purpose"])

    return SubagentSpec(
        name="general-purpose",
        description="Default sub-agent (no presets configured).",
        max_turns=200,
    )


def _spec_from_preset(preset: Any) -> SubagentSpec:
    return SubagentSpec(
        name=preset.name,
        description=preset.description,
        instructions=preset.instructions,
        instructions_mode=preset.instructions_mode,
        tools_allow=list(preset.tools_allow) if preset.tools_allow is not None else None,
        tools_deny=list(preset.tools_deny),
        model=preset.model,
        thinking_level=preset.thinking_level,
        max_turns=preset.max_turns,
    )


def _build_subagent_tool_list(parent_ctx: DispatcherContext, spec: SubagentSpec) -> list[Any]:
    """Build the sub-agent's tool list from its spec."""
    from vtx.tools import DEFAULT_TOOLS, tools_by_name

    base_pool: dict[str, Any] = dict(tools_by_name)
    base_names: list[str] = list(DEFAULT_TOOLS)

    # Avoid recursive dispatch by denying the task tool to sub-agents
    if "task" in base_pool:
        base_pool.pop("task")
    if "task" in base_names:
        base_names.remove("task")

    extension_tools: list[Any] = []

    from vtx.agents.activate import compose_active_tools
    from vtx.agents.api import LoadedAgent

    loaded_stub = LoadedAgent(definition=spec.to_agentdef(), path=Path("<task-subagent>"))
    return compose_active_tools(
        base_tool_names=base_names,
        base_tool_pool=base_pool,
        extension_tools=extension_tools,
        active_agent=loaded_stub,
    )


SUBAGENT_FINAL_ANSWER_DIRECTIVE = (
    "You are an isolated sub-agent dispatched by a parent agent via the "
    "Task tool. Your final text response is returned to the parent agent "
    "verbatim as the tool result, so:\n"
    "\n"
    "  - Return ONLY your final answer — no preamble, no 'I will now...', "
    "no step-by-step narration of the work you did.\n"
    "  - Do not summarise the tools you called or the files you read; the "
    "parent does not see those and they would just clutter the answer.\n"
    "  - If the request was open-ended, prefer a focused, self-contained "
    "answer that another agent can act on without further questions.\n"
    "  - Tool calls are fine — they let you actually do the work — but the "
    "final text is what the parent reads."
)


def _build_subagent_system_prompt(
    parent_ctx: DispatcherContext, spec: SubagentSpec, tools: list[Any]
) -> str:
    """Build the sub-agent's system prompt."""
    from vtx.prompts import build_system_prompt

    extra = spec.instructions
    mode = spec.instructions_mode
    if mode == "replace":
        return (
            build_system_prompt(
                parent_ctx.cwd,
                context=None,
                tools=tools,
                extra_instructions=extra,
                extra_instructions_mode="replace",
            )
            + "\n\n"
            + SUBAGENT_FINAL_ANSWER_DIRECTIVE
        )

    base = parent_ctx.system_prompt or build_system_prompt(
        parent_ctx.cwd, context=None, tools=tools
    )
    parts = [base.rstrip()]
    if extra:
        parts.append(extra.strip())
    parts.append(SUBAGENT_FINAL_ANSWER_DIRECTIVE)
    return "\n\n".join(parts) + "\n"


def _create_subagent_session(parent_ctx: DispatcherContext) -> Session:
    """Create a fresh, persisted :class:`Session` for the sub-agent."""
    from vtx.config import get_config_dir
    from vtx.session import Session

    safe_cwd = parent_ctx.cwd.replace("/", "-").replace("\\", "-").strip("-") or "root"
    tasks_dir = get_config_dir() / "tasks" / safe_cwd
    tasks_dir.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        tasks_dir.chmod(0o700)

    return Session.create(
        cwd=parent_ctx.cwd,
        provider=parent_ctx.model_provider,
        model_id=parent_ctx.model,
        thinking_level=parent_ctx.thinking_level or "high",
        system_prompt=None,
        tools=None,
    )


@dataclass
class SubagentRunResult:
    """Internal result of a sub-agent run."""

    final_text: str = ""
    turns: int = 0
    usage: Usage = field(default_factory=Usage)
    stop_reason: StopReason = StopReason.STOP
    transcript: list[str] = field(default_factory=list)
    error: str | None = None
    session_id: str | None = None


def _resolve_api_and_base_url(
    model: str, provider: str | None, parent_base_url: str | None
) -> tuple[Any, str | None]:
    """Resolve ``(api_type, effective_base_url)`` for a model + provider."""
    from vtx.llm import get_model, resolve_provider_api_type
    from vtx.llm.dynamic_models import find_dynamic_model
    from vtx.runtime import default_base_url_for_api, default_base_url_for_provider

    model_info = get_model(model, provider)
    if model_info:
        return model_info.api, parent_base_url or model_info.base_url
    dynamic = find_dynamic_model(model, provider)
    if dynamic is not None:
        return dynamic.api, parent_base_url or dynamic.base_url
    api_type = resolve_provider_api_type(provider)
    provider_default = default_base_url_for_provider(provider)
    return (api_type, parent_base_url or provider_default or default_base_url_for_api(api_type))


async def _run_subagent(
    parent_ctx: DispatcherContext,
    spec: SubagentSpec,
    prompt: str,
    cancel_event: asyncio.Event | None,
    model_override: str | None,
    progress_callback: Callable[[str, dict], None] | None,
    tool_call_id: str,
) -> SubagentRunResult:
    """Run a single sub-agent to completion."""
    from dataclasses import replace as dc_replace

    from vtx.llm import get_max_tokens
    from vtx.loop import Agent
    from vtx.runtime import create_provider

    tools = _build_subagent_tool_list(parent_ctx, spec)
    system_prompt = _build_subagent_system_prompt(parent_ctx, spec, tools)
    session = _create_subagent_session(parent_ctx)

    sub_model = model_override or spec.model or parent_ctx.model
    sub_thinking = spec.thinking_level or parent_ctx.thinking_level

    parent_config = parent_ctx.provider.config
    sub_config = dc_replace(
        parent_config,
        model=sub_model,
        thinking_level=sub_thinking,
        max_tokens=get_max_tokens(sub_model),
        session_id=session.id,
    )
    api_type, effective_base_url = _resolve_api_and_base_url(
        sub_model, parent_ctx.model_provider, parent_ctx.base_url
    )
    if effective_base_url:
        sub_config = dc_replace(sub_config, base_url=effective_base_url)
    provider = create_provider(api_type, sub_config)

    sub_agent = Agent(
        provider=provider,
        tools=tools,
        session=session,
        cwd=parent_ctx.cwd,
        system_prompt=system_prompt,
    )

    result = SubagentRunResult(session_id=session.id)
    transcript = result.transcript

    def _emit(progress_kind: str, **fields: Any) -> None:
        if progress_callback is None:
            return
        try:
            progress_callback(
                tool_call_id, {"kind": progress_kind, "subagent": spec.name, **fields}
            )
        except Exception:
            log.exception("Task tool progress callback raised")

    _emit("subagent_start", description=spec.description)

    try:
        from vtx.events import (
            AgentEndEvent,
            ErrorEvent,
            InterruptedEvent,
            TextDeltaEvent,
            ToolResultEvent,
            ToolStartEvent,
            TurnEndEvent,
        )

        async for event in sub_agent.run(prompt, cancel_event=cancel_event):
            if isinstance(event, TextDeltaEvent):
                _emit("text_delta", delta=event.delta)
            elif isinstance(event, ToolStartEvent):
                transcript.append(f"  → {event.tool_name}")
                _emit("tool_start", tool_name=event.tool_name)
            elif isinstance(event, ToolResultEvent):
                _emit("tool_result", tool_name=event.tool_name)
            elif isinstance(event, TurnEndEvent):
                result.turns += 1
                if event.assistant_message is not None:
                    msg = event.assistant_message
                    if msg.usage is not None:
                        result.usage.input_tokens += msg.usage.input_tokens
                        result.usage.output_tokens += msg.usage.output_tokens
                        result.usage.cache_read_tokens += msg.usage.cache_read_tokens
                        result.usage.cache_write_tokens += msg.usage.cache_write_tokens
                    if msg.stop_reason != StopReason.TOOL_USE:
                        text_parts: list[str] = []
                        for part in msg.content:
                            if isinstance(part, TextContent):
                                text_parts.append(part.text)
                        result.final_text = "".join(text_parts)
            elif isinstance(event, AgentEndEvent):
                result.stop_reason = event.stop_reason
            elif isinstance(event, ErrorEvent):
                result.error = event.error
                _emit("error", error=event.error)
            elif isinstance(event, InterruptedEvent):
                result.error = "Sub-agent interrupted."
                _emit("interrupted")
                break
    except asyncio.CancelledError:
        result.error = "Sub-agent cancelled."
        _emit("cancelled")
        raise
    except Exception as exc:
        result.error = f"Sub-agent raised: {exc}"
        log.exception("Task tool sub-agent run failed")
        _emit("error", error=result.error)

    _emit("subagent_end", turns=result.turns, stop_reason=result.stop_reason.value)
    return result


class TaskTool(BaseTool[TaskParams]):
    name = "task"
    params = TaskParams
    tool_icon = "⊕"
    mutating = False

    @property
    def ui_block(self) -> type | None:
        from ..ui.blocks import TaskToolBlock

        return TaskToolBlock

    description = (
        "Dispatch a fresh sub-agent (own tools/session, can't see this chat) for a "
        "self-contained task; returns only its final text. background: true returns "
        "a task_id and delivers the answer next turn. Not for trivial single-tool calls."
    )

    prompt_guidelines = ()

    def format_call(self, params: TaskParams) -> str:
        return params.description

    async def execute(
        self, params: TaskParams, cancel_event: asyncio.Event | None = None
    ) -> ToolResult:
        parent_ctx = get_context()
        if parent_ctx is None:
            return ToolResult(
                success=False,
                result=(
                    "Task tool invoked before the parent context was "
                    "initialized. This usually means the TUI/headless "
                    "runtime never set the dispatcher context; please "
                    "report it as a bug."
                ),
            )

        if params.background:
            return await self._execute_background(params, parent_ctx)

        tool_call_id = f"task_{uuid.uuid4().hex[:12]}"

        spec = _resolve_subagent_spec(params.subagent_type, parent_ctx.agent_registry)
        progress_cb = parent_ctx.progress_callback

        sub_result = await _run_subagent(
            parent_ctx=parent_ctx,
            spec=spec,
            prompt=params.prompt,
            cancel_event=cancel_event,
            model_override=params.model,
            progress_callback=progress_cb,
            tool_call_id=tool_call_id,
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
            f"{sub_result.turns} turn{'s' if sub_result.turns != 1 else ''}, "
            f"{_format_tokens(sub_result.usage.total_tokens)}"
        )
        if sub_result.error is not None:
            ui_summary += f" — {sub_result.error[:40]}"

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

    async def _execute_background(self, params: TaskParams, parent_ctx: Any) -> ToolResult:
        """Dispatch a sub-agent and return immediately with a ``task_id``.

        The sub-agent runs concurrently on the asyncio loop; the
        parent turn is unblocked. Completion is reported via a
        :class:`~vtx.events.BackgroundTaskCompletedEvent` yielded
        between parent turns by :meth:`vtx.loop.Agent.run`, and the
        final answer is delivered automatically via a synthetic message.

        Cancellation contract: a background task is not cancelled
        when the parent's ``cancel_event`` fires (the call has
        already returned). It is only cancelled by an explicit
        stop or by :meth:`vtx.runtime.ConversationRuntime.close`.
        """
        from .background import get_manager

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

        spec = _resolve_subagent_spec(params.subagent_type, parent_ctx.agent_registry)
        tool_call_id = f"task_{uuid.uuid4().hex[:12]}"
        progress_cb = parent_ctx.progress_callback
        parent_session_id = getattr(parent_ctx, "session_id", None)
        if parent_session_id is None and getattr(parent_ctx, "session", None) is not None:
            parent_session_id = parent_ctx.session.id

        async def _factory() -> Any:
            # Background sub-agents run with no cancellation signal
            # from the parent — they survive Esc and only stop on
            # explicit /tasks stop or runtime close.
            return await _run_subagent(
                parent_ctx=parent_ctx,
                spec=spec,
                prompt=params.prompt,
                cancel_event=None,
                model_override=params.model,
                progress_callback=progress_cb,
                tool_call_id=tool_call_id,
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
            ui_details=None,
            ui_details_full=(
                f"task_id: {record.task_id}\n"
                f"description: {record.description}\n"
                f"subagent_type: {spec.name}\n"
                f"model: {params.model or spec.model or parent_ctx.model}\n"
                f"record: {manager._store_dir}/{record.task_id}.json"
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

"""Sub-agent dispatch builder: spec resolution, prompting, sessions, and runs.

This is the harness-side machinery behind the ``delegate_subagent`` tool. The
tool itself is a product concern and lives with the product package; products
build their tool on top of :class:`SubagentSpec`, :func:`resolve_subagent_spec`,
:func:`resolve_subagent_runner`, and :class:`SubagentRunResult`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ctx.agent.dispatcher import DispatcherContext
from ctx.agent.subagents import get_scheduler
from ctx.protocol.types import StopReason, TextContent, Usage

if TYPE_CHECKING:
    from ctx.agent.session import Session
    from ctx.ai.base import BaseProvider  # noqa: F401

log = logging.getLogger("agent.subagent")


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

        from ctx.agent.agents.schema import AgentDef, InstructionsMode, ThinkingLevel

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


#: Name of the sub-agent used when ``subagent_type`` matches no user agent.
DEFAULT_SUBAGENT = "subagent"

#: How many turns a default sub-agent gets before it is cut off.
DEFAULT_MAX_TURNS = 200


def _default_subagent_spec() -> SubagentSpec:
    return SubagentSpec(
        name=DEFAULT_SUBAGENT,
        description="Default sub-agent: the parent's tools and instructions.",
        max_turns=DEFAULT_MAX_TURNS,
    )


def resolve_subagent_spec(subagent_type: str, registry: Any) -> SubagentSpec:
    """Resolve ``subagent_type`` to a concrete :class:`SubagentSpec`.

    Only user-defined agents (``.ctx/agent/<name>.py``) name a sub-agent. There
    are no built-in presets: anything the registry does not know about runs the
    default sub-agent, which is the parent minus the parent-only tools.
    """
    cleaned = (subagent_type or "").strip()
    if not cleaned or registry is None:
        return _default_subagent_spec()

    loaded = registry.by_name(cleaned)
    if loaded is None:
        return _default_subagent_spec()

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


def _build_subagent_tool_list(parent_ctx: DispatcherContext, spec: SubagentSpec) -> list[Any]:
    from ctx.agent.tools import get_all_tools, get_default_tools, get_parent_only_tools

    parent_only = get_parent_only_tools()
    base_pool = {name: tool for name, tool in get_all_tools().items() if name not in parent_only}
    base_names = [n for n in get_default_tools() if n not in parent_only]

    extension_tools: list[Any] = []

    from ctx.agent.agents.activate import compose_active_tools
    from ctx.agent.agents.api import LoadedAgent

    loaded_stub = LoadedAgent(definition=spec.to_agentdef(), path=Path("<task-subagent>"))
    return compose_active_tools(
        base_tool_names=base_names,
        base_tool_pool=base_pool,
        extension_tools=extension_tools,
        active_agent=loaded_stub,
    )


SUBAGENT_FINAL_ANSWER_DIRECTIVE = (
    "You are an isolated sub-agent dispatched by a parent agent via the "
    "Subagent dispatch. Your final text response is returned to the parent agent "
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
    from ctx.agent.prompts import build_system_prompt

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
    from ctx.agent.session import Session
    from ctx.core.paths import get_config_dir

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


def _charge_parent_session(
    parent_ctx: DispatcherContext,
    result: SubagentRunResult,
    *,
    tool_call_id: str | None,
    label: str,
) -> None:
    """Charge a finished sub-agent's tokens to the session that dispatched it.

    A sub-agent runs in its own persisted session, so its assistant messages -
    and its usage - never land in the dispatching session's transcript.
    ``Session.token_totals`` reads only that session's entries, so "this session"
    in ``/usage`` would exclude every sub-agent it dispatched and disagree with
    the lifetime figure, which is read from every session log and does include
    them.

    The sub-agent's own log still carries the per-message breakdown and
    ``aggregate_usage`` reads that file separately, so this does not double-count
    the lifetime total. Charged even on failure, because the tokens were spent
    either way. A context with no session (headless harnesses without a runtime)
    is a no-op rather than an error.
    """
    parent_session = getattr(parent_ctx, "session", None)
    record = getattr(parent_session, "record_subagent_usage", None)
    if not callable(record):
        return
    with contextlib.suppress(Exception):
        record(
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
            cache_read_tokens=result.usage.cache_read_tokens,
            cache_write_tokens=result.usage.cache_write_tokens,
            turns=result.turns,
            task_id=tool_call_id,
            label=label,
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
    duration_ms: float | None = None


def _resolve_api_and_base_url(
    model: str, provider: str | None, parent_base_url: str | None
) -> tuple[Any, str | None]:
    """Resolve ``(api_type, effective_base_url)`` for a model + provider."""
    from ctx.agent.runtime import default_base_url_for_api, default_base_url_for_provider
    from ctx.ai import get_model, resolve_provider_api_type
    from ctx.ai.dynamic_models import find_dynamic_model

    model_info = get_model(model, provider)
    if model_info:
        return model_info.api, parent_base_url or model_info.base_url
    dynamic = find_dynamic_model(model, provider)
    if dynamic is not None:
        return dynamic.api, parent_base_url or dynamic.base_url
    api_type = resolve_provider_api_type(provider)
    provider_default = default_base_url_for_provider(provider)
    return (api_type, parent_base_url or provider_default or default_base_url_for_api(api_type))


def _make_progress_emitter(
    spec: SubagentSpec, tool_call_id: str, progress_callback: Callable[[str, dict], None] | None
) -> Callable[..., None]:
    def _emit(progress_kind: str, **fields: Any) -> None:
        if progress_callback is None:
            return
        try:
            progress_callback(
                tool_call_id, {"kind": progress_kind, "subagent": spec.name, **fields}
            )
        except Exception:
            log.exception("subagent dispatch progress callback raised")

    return _emit


# Product layers register a replacement runner here instead of the harness
# importing them. The old mechanism had each call site re-import
# ctx.agent.tools.task looking for a divergent _run_subagent, but that
# module re-exports this very function, so the lookup could never differ and
# the harness still held a back-edge to the product layer.
_subagent_runner_override: Callable[..., Any] | None = None


def set_subagent_runner(runner: Callable[..., Any] | None) -> None:
    """Override the sub-agent runner (product layer or tests)."""
    global _subagent_runner_override
    _subagent_runner_override = runner


def resolve_subagent_runner() -> Callable[..., Any]:
    return _subagent_runner_override or _run_subagent


async def _run_subagent(
    parent_ctx: DispatcherContext,
    spec: SubagentSpec,
    prompt: str,
    cancel_event: asyncio.Event | None,
    model_override: str | None,
    progress_callback: Callable[[str, dict], None] | None,
    tool_call_id: str,
    label: str = "",
) -> SubagentRunResult:
    """Admit one sub-agent to the shared scheduler, then run it to completion.

    Dispatching is cheap but running is not: a fan-out of a dozen sub-agents
    opened a dozen provider streams at once. Waiting for a slot here (rather
    than inside the run) means a queued sub-agent has not yet built a session
    or a provider, and both the chat block and the pinned Agents panel can
    report it as queued instead of pretending to be working.

    ``label`` is the dispatch's 3-5 word task description. It rides along
    because it is what a human recognises the row by; the spec description is
    boilerplate about the agent profile, not about this run.
    """
    scheduler = get_scheduler()
    emit = _make_progress_emitter(spec, tool_call_id, progress_callback)

    sub_model = model_override or spec.model or parent_ctx.model
    if not scheduler.admits_now():
        emit(
            "subagent_queued",
            description=spec.description,
            label=label,
            model=sub_model,
            max_turns=spec.max_turns,
            position=scheduler.queued + 1,
        )

    await scheduler.acquire()
    try:
        return await _run_admitted_subagent(
            parent_ctx=parent_ctx,
            spec=spec,
            prompt=prompt,
            cancel_event=cancel_event,
            model_override=model_override,
            progress_callback=progress_callback,
            tool_call_id=tool_call_id,
            label=label,
        )
    finally:
        scheduler.release()


async def _run_admitted_subagent(
    parent_ctx: DispatcherContext,
    spec: SubagentSpec,
    prompt: str,
    cancel_event: asyncio.Event | None,
    model_override: str | None,
    progress_callback: Callable[[str, dict], None] | None,
    tool_call_id: str,
    label: str = "",
) -> SubagentRunResult:
    """Run a single sub-agent to completion."""
    from dataclasses import replace as dc_replace

    from ctx.agent.loop import Agent
    from ctx.agent.runtime import create_provider
    from ctx.ai import get_max_tokens

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
        # Depth 1: a child inherits the parent's session-level state rather than
        # owning it, so it neither runs the skills-refresh gate nor drains the
        # parent's pending queue.
        depth=1,
    )

    result = SubagentRunResult(session_id=session.id)
    transcript = result.transcript

    _emit = _make_progress_emitter(spec, tool_call_id, progress_callback)
    tool_counts: dict[str, int] = {}

    _emit(
        "subagent_start",
        description=spec.description,
        label=label,
        model=sub_model,
        max_turns=spec.max_turns,
        tool_counts=dict(tool_counts),
    )

    started_at = time.monotonic()

    try:
        from ctx.core import (
            AgentEndEvent,
            ApprovalResponse,
            AskUserEvent,
            AskUserResponse,
            ErrorEvent,
            InterruptedEvent,
            TextDeltaEvent,
            ToolApprovalEvent,
            ToolResultEvent,
            ToolStartEvent,
            TurnEndEvent,
        )

        async for event in sub_agent.run(prompt, cancel_event=cancel_event):
            if isinstance(event, TextDeltaEvent):
                _emit("text_delta", delta=event.delta)
            elif isinstance(event, ToolApprovalEvent):
                if event.future is not None and not event.future.done():
                    event.future.set_result(ApprovalResponse.APPROVE)
            elif isinstance(event, AskUserEvent):
                if event.future is not None and not event.future.done():
                    event.future.set_result(AskUserResponse())
            elif isinstance(event, ToolStartEvent):
                tool_counts[event.tool_name] = tool_counts.get(event.tool_name, 0) + 1
                transcript.append(f"  → {event.tool_name}")
                _emit("tool_start", tool_name=event.tool_name, tool_counts=dict(tool_counts))
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
                _emit(
                    "turn_end",
                    turns=result.turns,
                    tokens=result.usage.total_tokens,
                    tool_counts=dict(tool_counts),
                )
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
        log.exception("subagent dispatch failed")
        _emit("error", error=result.error)

    result.duration_ms = (time.monotonic() - started_at) * 1000

    _charge_parent_session(parent_ctx, result, tool_call_id=tool_call_id, label=label)

    _emit(
        "subagent_end",
        turns=result.turns,
        stop_reason=result.stop_reason.value,
        final_text=result.final_text,
        tool_counts=dict(tool_counts),
        tokens=result.usage.total_tokens,
        transcript=list(transcript),
    )
    return result

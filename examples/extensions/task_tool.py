"""Example extension: the ``Task`` tool (Claude Code-style sub-agents).

This file is **not part of vtx**. It's a third-party-style extension
that demonstrates how to build a non-trivial tool on top of vtx's
``ExtensionAPI`` and the generic :mod:`vtx.dispatcher` infrastructure.

To use it, copy this file to one of:

* ``<project>/.vtx/extensions/task_tool.py`` — project-local
* ``~/.vtx/agent/extensions/task_tool.py`` — global, available in
  every project

vtx will auto-discover and load it on every run. The LLM will then
have a ``Task`` tool available with the surface described below.

The :mod:`vtx.dispatcher` slot is a vtx platform feature — the
runtime populates it with the parent's provider, model, cwd,
agent-registry, etc. on every state change. The tool reads from
this slot when it dispatches a sub-agent.

---

The ``Task`` tool surface (Claude Code-compatible):

* ``description`` (str, required) — 3-5 word imperative-cased label
* ``prompt`` (str, required) — the actual instructions for the
  sub-agent
* ``subagent_type`` (str, optional) — preset name or user-defined
  agent name. Defaults to ``"general-purpose"``.
* ``model`` (str, optional) — model override; falls back to the
  parent's

The ``subagent_type`` lookup is, in order:

1. A user-defined agent from ``.vtx/agent/<name>.py`` (the
   :class:`~vtx.agents.AgentRegistry` contents).
2. A built-in preset from ``vtx.config.task.subagent_presets``.
3. The ``"general-purpose"`` preset as a final fallback.

Synchronous in v1: the parent blocks until the sub-agent returns.
The sub-agent's session is persisted under
``~/.vtx/tasks/<safe_cwd>/`` so its full transcript survives across
runs.

The sub-agent's final text is the only thing the LLM-facing tool
result contains — no preamble, no transcript, no metadata. The
full transcript is preserved in ``ui_details`` for the TUI only.
"""

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

# vtx platform imports — the extension uses these to dispatch
# sub-agents, but everything else (the ``TaskTool`` class, the
# ``SubagentSpec`` dataclass, the run loop) lives in this file.
from vtx import config as vtx_config
from vtx.agents.activate import compose_active_tools
from vtx.agents.api import LoadedAgent
from vtx.core.types import StopReason, TextContent, ToolResult, Usage
from vtx.dispatcher import DispatcherContext, get_context
from vtx.session import Session

if TYPE_CHECKING:
    from vtx.llm.base import BaseProvider  # noqa: F401
    from vtx.tools.base import BaseTool  # noqa: F401

log = logging.getLogger("task_tool.extension")

# Maximum characters of the sub-agent's final text we hand back to
# the LLM as a tool result. A runaway sub-agent must not blow the
# parent's context window. The full transcript remains available in
# ``ui_details`` for the TUI.
MAX_RESULT_CHARS = 32_000
MAX_TRANSCRIPT_LINES = 200


# ---------------------------------------------------------------------------
# Tool params (Pydantic)
# ---------------------------------------------------------------------------


class TaskParams(BaseModel):
    description: str = Field(
        min_length=1,
        max_length=128,
        description=(
            "A short (3-5 word) description of what the sub-agent will do. "
            "Shown in the UI as the tool's label. Use imperative-cased form "
            "(e.g. 'Find the auth bug')."
        ),
    )
    prompt: str = Field(
        min_length=1,
        description=(
            "The instructions for the sub-agent. Be specific about what it "
            "should do, what it should look for, and what shape the answer "
            "should take. The sub-agent cannot see this conversation's "
            "history; include any context it needs."
        ),
    )
    subagent_type: str = Field(
        default="general-purpose",
        description=(
            "Which sub-agent to dispatch. Either a user-defined agent name "
            "(from .vtx/agent/<name>.py) or a built-in preset: "
            "'general-purpose', 'Explore', or 'Plan'. Defaults to "
            "'general-purpose'."
        ),
    )
    model: str | None = Field(
        default=None,
        description=("Optional model override for the sub-agent. Defaults to the parent's model."),
    )


# ---------------------------------------------------------------------------
# Resolved sub-agent spec — internal type used to drive a sub-agent run.
# We use a dataclass (not AgentDef) so preset names like 'Plan' and
# 'Explore' don't have to satisfy AgentDef's lowercase agent-name regex.
# ---------------------------------------------------------------------------


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
        """Lift the spec into an :class:`AgentDef` for the tool composer.

        AgentDef's validator wants a lowercase, hyphenated name. Preset
        names like 'Plan' and 'Explore' don't satisfy that, so we
        lowercase them when building the AgentDef — the lowercase form
        is only used internally to drive the tool-composition helpers.
        """
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


# ---------------------------------------------------------------------------
# Sub-agent construction
# ---------------------------------------------------------------------------


def _resolve_subagent_spec(subagent_type: str, registry: Any) -> SubagentSpec:
    """Resolve ``subagent_type`` to a concrete :class:`SubagentSpec`.

    Lookup order: user-defined agent → built-in preset → general-purpose
    fallback. The returned spec is always usable as a fully-formed
    profile.
    """
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

    # Custom config wiped the presets out — fall back to a permissive
    # in-code default so the tool still works.
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
    """Build the sub-agent's tool list from its spec.

    Mirrors :func:`vtx.agents.activate.compose_active_tools`: built-in
    base tools first, then extension tools (none for the child by
    default — that would leak the parent's profile), with the spec's
    ``tools_allow`` / ``tools_deny`` filters applied. The child never
    sees the ``Task`` tool itself, so it cannot recursively dispatch.

    Imports are deferred to avoid a circular import at module load
    time.
    """
    from vtx.tools import DEFAULT_TOOLS, tools_by_name

    base_pool: dict[str, Any] = dict(tools_by_name)
    base_names: list[str] = list(DEFAULT_TOOLS)

    # The sub-agent does not inherit the parent's session extensions or
    # active agent profile. Starting from a clean base pool is exactly
    # the right behavior for an isolated sub-agent.
    extension_tools: list[Any] = []

    loaded_stub = LoadedAgent(definition=spec.to_agentdef(), path=Path("<task-subagent>"))
    return compose_active_tools(
        base_tool_names=base_names,
        base_tool_pool=base_pool,
        extension_tools=extension_tools,
        active_agent=loaded_stub,
    )


# Directive appended to every sub-agent's system prompt by default.
# The sub-agent's final text is what the parent LLM sees as the tool
# result, so we want it to be a clean, self-contained answer — not a
# transcript of tool calls or intermediate reasoning.
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
    """Build the sub-agent's system prompt from the parent's base + the
    sub-agent's instructions overlay.

    We always append :data:`SUBAGENT_FINAL_ANSWER_DIRECTIVE` so the
    sub-agent's final text is a clean answer to the parent's request.
    """
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


# ---------------------------------------------------------------------------
# Sub-agent run loop
# ---------------------------------------------------------------------------


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
    """Resolve ``(api_type, effective_base_url)`` for a model + provider.

    Mirrors :meth:`vtx.runtime.ConversationRuntime._model_api_and_base_url`
    but without needing a runtime instance.
    """
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

    # Clone the parent's provider config and re-resolve api_type +
    # base_url for the (possibly different) sub-model. We always build
    # a fresh provider for the sub-agent so a model override doesn't
    # mutate the parent's provider state.
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
                    # Only update final_text on turns where the
                    # model isn't about to call another tool. We
                    # want the FULL text the model generated in the
                    # turn *after* its last tool call — every
                    # TextContent part concatenated.
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


# ---------------------------------------------------------------------------
# TaskTool — a regular class the extension registers via
# ExtensionAPI.register_tool. NOT a vtx built-in.
# ---------------------------------------------------------------------------


class TaskTool:
    """Dispatch a sub-agent and return its result.

    A plain class (not a ``BaseTool`` subclass) because we bridge it
    through :class:`vtx.extensions.ExtensionTool` — the extension's
    ``execute`` callback adapts ``(args, ctx)`` to this class's
    typed ``execute(params, cancel_event)`` signature.

    See the module docstring for the full contract.
    """

    description = (
        "Dispatch a fresh sub-agent to handle a self-contained task. The "
        "sub-agent has its own tool surface, runs in its own session, and "
        "cannot see the current conversation. The tool returns ONLY the "
        "sub-agent's final text — no preamble, no transcript of tool "
        "calls, no metadata. The full transcript is preserved in the "
        "sub-agent's session file (``~/.vtx/tasks/``) for inspection. "
        "Provide a clear ``prompt`` with all necessary context, pick a "
        "``subagent_type`` (``general-purpose``, ``Explore``, ``Plan``, or "
        "a user-defined agent name from .vtx/agent/), and an optional "
        "``model`` override. Use this for parallelisable, well-scoped "
        "work; do not use it for trivial single-tool calls."
    )
    prompt_guidelines = (
        "Use the Task tool to delegate well-scoped work to a sub-agent when: "
        "(1) the task is independent of the current conversation context, "
        "(2) the result fits in a single tool-result message, "
        "(3) running the work inline would clutter the parent's tool log, "
        "or (4) you want a different tool surface or model than the parent. "
        "Prefer a specific subagent_type (Explore for read-only repo "
        "navigation, Plan for read-only investigation that produces a "
        "plan, general-purpose for everything else) over a custom agent. "
        "Never use Task for trivial single-tool work — call the tool "
        "directly. The sub-agent cannot see this conversation; include "
        "all context the sub-agent needs in the prompt."
    )
    tool_icon = "⊕"

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

        # Tool call id doubles as the sub-agent task id. We need a
        # stable id before the run starts so the sub-agent's session
        # file and the progress callback events line up.
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
                ui_details=_format_transcript(sub_result.transcript),
            )

        # The LLM-facing ``result`` is the sub-agent's *final text
        # only* — no preamble, no transcript, no truncation markers.
        # The full transcript lives in ``ui_details`` for the TUI
        # only. We still cap the size so a runaway sub-agent can't
        # blow the parent's context, but the cut is silent.
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
            ui_details=ui_details,
        )


def _format_tokens(n: int) -> str:
    if n < 1000:
        return f"{n}t"
    if n < 10_000:
        return f"{n / 1000:.1f}k"
    return f"{round(n / 1000)}k"


def _format_transcript(transcript: list[str], header: str | None = None) -> str:
    """Render a sub-agent's tool-call transcript for the TUI's tool-block
    details panel.
    """
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


# ---------------------------------------------------------------------------
# Extension entry point
# ---------------------------------------------------------------------------


def register(api: Any) -> None:
    """Register the bundled ``task`` tool with the runtime.

    The extension's ``execute`` callback bridges the extension tool
    shape ``(args_dict, ctx_dict) -> dict`` to
    :meth:`TaskTool.execute`'s typed signature
    ``(TaskParams, cancel_event) -> ToolResult``.
    """

    async def _execute(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
        cancel = (ctx or {}).get("cancel_event")
        params = TaskParams(**args)
        # TaskTool is stateless (it reads the dispatcher context from
        # the vtx platform slot on each call), so a fresh instance
        # per call is fine.
        tool = TaskTool()
        result = await tool.execute(params, cancel_event=cancel)
        return {
            "success": result.success,
            "result": result.result,
            "ui_summary": result.ui_summary,
            "ui_details": result.ui_details,
            "ui_details_full": result.ui_details_full,
            "images": result.images,
        }

    api.register_tool(
        name="task",
        description=TaskTool.description,
        parameters=TaskParams.model_json_schema(),
        execute=_execute,
        # The Task tool itself is mutating=False (dispatching a
        # sub-agent is not a "you should approve this" action from the
        # parent's perspective). The sub-agent's mutability is
        # controlled by its own tools_allow/tools_deny.
        mutating=False,
        label="task",
        # Match the icon used in the original in-tree TaskTool so the
        # TUI shows the familiar "⊕" in the tool header.
        tool_icon="⊕",
    )

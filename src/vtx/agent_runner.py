"""Provider-agnostic single-turn execution engine.

This mirrors the *engine* layer of ``vtx_claw`` (``AgentRunner``): a thin,
transport-free function that runs one LLM turn and yields its events. The
product/transport loop in :mod:`vtx.loop` owns sessions, UI events, goals and
persistence; this module owns only the turn itself.

Today this delegates to :func:`vtx.turn.run_single_turn` so behavior is
unchanged. Later phases (stop-condition recovery, mid-turn injection,
checkpoint/resume, context governance) extend *this* function without touching
the TUI/headless/sdk callers of the loop.
"""

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from .core.types import AssistantMessage, Message, StopReason, ToolResultMessage
from .events import Event, StreamEvent
from .extensions import EventBus
from .hooks.agent_hook import AgentHook
from .llm import BaseProvider
from .tools import BaseTool
from .turn import run_single_turn


@dataclass
class AgentRunSpec:
    """Input contract for one engine turn.

    Intentionally small and additive — later phases add optional fields
    (injection_callback, checkpoint_callback, hooks) without breaking callers
    that only pass the required core fields.
    """

    provider: BaseProvider
    messages: list[Message]
    tools: list[BaseTool]
    system_prompt: str | None = None
    turn: int = 0
    cancel_event: asyncio.Event | None = None
    extensions: EventBus | None = None
    retry_delays: list[int] | None = None
    # Optional callback polled after tool execution to inject follow-up user
    # messages (sub-agent completions, queued prompts) mid-turn. Returning a
    # non-empty list of messages extends the turn instead of ending it.
    injection_callback: Callable[[], Any] | None = None
    # Optional callback invoked after each tool batch to persist partial
    # turn state (text so far + tool results) for cancel/resume. Receives a
    # dict snapshot; return value is ignored.
    checkpoint_callback: Callable[[dict[str, Any]], Any] | None = None
    # In-process lifecycle hooks (vtx_claw-style AgentHook). Fired by the
    # engine around run/iteration boundaries and stream deltas.
    hooks: list[AgentHook] | None = None


@dataclass
class AgentRunResult:
    """Output contract for one engine turn.

    ``events`` carries the fully-typed stream (think/text/tool UI events) the
    loop forwards verbatim. ``assistant_message`` / ``tool_results`` /
    ``stop_reason`` are the structured outcome the loop persists and branches on.

    Not yet returned to the loop (the loop reads these from the yielded
    :class:`~vtx.events.TurnEndEvent`); it becomes the engine's return value
    once later phases wrap the turn in their own recovery/injection loop.
    """

    events: list[StreamEvent] = field(default_factory=list)
    assistant_message: AssistantMessage | None = None
    tool_results: list[ToolResultMessage] = field(default_factory=list)
    stop_reason: StopReason = StopReason.STOP


async def run_agent_turn(spec: AgentRunSpec) -> AsyncIterator[Event]:
    """Execute one turn and yield its events.

    Faithful delegation to :func:`vtx.turn.run_single_turn`: same
    :class:`~vtx.events.StreamEvent` sequence, same terminal
    :class:`~vtx.events.TurnEndEvent`. The loop keeps consuming that event
    unchanged. Later phases will own the recovery/injection loop here instead
    of inside the turn module.
    """
    async for event in run_single_turn(
        provider=spec.provider,
        messages=spec.messages,
        tools=spec.tools,
        system_prompt=spec.system_prompt,
        turn=spec.turn,
        cancel_event=spec.cancel_event,  # type: ignore[arg-type]
        retry_delays=spec.retry_delays,
        extensions=spec.extensions,
        injection_callback=spec.injection_callback,
        checkpoint_callback=spec.checkpoint_callback,
        hooks=_build_composite(spec.hooks),
    ):
        yield event


def _build_composite(hooks: list[AgentHook] | None) -> AgentHook | None:
    """Build a CompositeHook from the spec hooks, or None if empty."""
    if not hooks:
        return None
    # Imported lazily to keep the engine import-light and avoid any chance of
    # a circular import at module load time.
    from .hooks.agent_hook import CompositeHook

    return CompositeHook(list(hooks))

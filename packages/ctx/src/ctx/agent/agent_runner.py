"""Provider-agnostic single-turn execution engine.

A thin, transport-free function that runs one LLM turn and yields its events.
The product/transport loop in :mod:`ctx.loop` owns sessions, UI events,
and persistence; this module owns only the turn itself.

Today this delegates to :func:`ctx.turn.run_single_turn` so behavior is
unchanged. Later phases (stop-condition recovery, mid-turn injection,
checkpoint/resume, context governance) extend *this* function without touching
the TUI/headless/sdk callers of the loop.
"""

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

from ctx.agent.hooks.agent_hook import AgentHook
from ctx.agent.tools import BaseTool
from ctx.ai import BaseProvider
from ctx.core.events import Event
from ctx.plugins.bus import EventBus
from ctx.protocol.types import Message


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
    # In-process lifecycle hooks (AgentHook). Fired by the engine around
    # run/iteration boundaries and stream deltas.
    hooks: list[AgentHook] | None = None


async def run_agent_turn(spec: AgentRunSpec) -> AsyncIterator[Event]:
    """Execute one turn and yield its events.

    Faithful delegation to :func:`ctx.turn.run_single_turn`: same
    :class:`~ctx.events.StreamEvent` sequence, same terminal
    :class:`~ctx.events.TurnEndEvent`. The loop keeps consuming that event
    unchanged. Later phases will own the recovery/injection loop here instead
    of inside the turn module.
    """
    from ctx.agent.turn import run_single_turn

    async for event in run_single_turn(
        provider=spec.provider,
        messages=spec.messages,
        tools=spec.tools,
        system_prompt=spec.system_prompt,
        turn=spec.turn,
        cancel_event=spec.cancel_event,
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
    from ctx.agent.hooks.agent_hook import CompositeHook

    return CompositeHook(list(hooks))

"""In-process agent hook API (vtx_claw-style AgentHook).

This is an *additive* lifecycle layer alongside the existing external YAML
hook system (``hooks/bridge.py`` + ``EventBus``). It lets Python callers
observe and (in one case) transform a run without going through the shell
hook machinery:

- ``before_run`` / ``after_run`` / ``on_error`` / ``on_finally`` — run scope
- ``before_iteration`` / ``after_iteration`` / ``before_execute_tools`` —
  per-iteration scope
- ``on_stream`` / ``on_stream_end`` — true token streaming deltas
- ``emit_reasoning`` — thinking/CoT deltas
- ``finalize_content`` — the ONLY flow-affecting hook; may rewrite the final
  assistant text (synchronous, returns the new content or ``None``)

``CompositeHook`` fans out to many hooks and isolates each one: a faulty hook
logs and is skipped instead of crashing the loop. A hook can opt into
re-raising with ``AgentHook(reraise=True)`` for cases where failure must
surface (e.g. a UI hook).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from ..core.types import Message, StopReason, ToolCall, ToolResultMessage, Usage

log = logging.getLogger("vtx.hooks.agent_hook")


@dataclass
class AgentHookContext:
    """Per-iteration state passed to iteration-scoped hooks."""

    turn: int
    iteration: int
    messages: list[Message] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_events: list[ToolResultMessage] = field(default_factory=list)


@dataclass
class AgentRunHookContext:
    """Run-scoped state passed to run-scoped hooks."""

    session_key: str | None = None
    model: str | None = None
    usage: Usage | None = None
    stop_reason: StopReason | None = None
    exception: BaseException | None = None
    error: str | None = None
    had_injections: bool = False


class AgentHook:
    """Base class for in-process lifecycle hooks. All methods are no-ops.

    Override only the events you care about. Async methods are ``await``ed by
    the engine; ``finalize_content`` is synchronous by design.
    """

    def __init__(self, reraise: bool = False) -> None:
        # Hooks that must surface failures (e.g. UI) set this; default swallows.
        self._reraise = reraise

    async def before_run(self, context: AgentRunHookContext) -> None: ...

    async def after_run(self, context: AgentRunHookContext) -> None: ...

    async def on_error(self, context: AgentRunHookContext) -> None: ...

    async def on_finally(self, context: AgentRunHookContext) -> None: ...

    async def before_iteration(self, context: AgentHookContext) -> None: ...

    async def after_iteration(self, context: AgentHookContext) -> None: ...

    async def before_execute_tools(self, context: AgentHookContext) -> None: ...

    async def on_stream(self, context: AgentHookContext, delta: str) -> None: ...

    async def on_stream_end(
        self, context: AgentHookContext, *, resuming: bool = False
    ) -> None: ...

    async def emit_reasoning(self, content: str) -> None: ...

    def finalize_content(self, context: AgentHookContext, content: str) -> str | None:
        """Optionally rewrite the final assistant text. Return new or None."""
        return None

    @property
    def reraise(self) -> bool:
        return self._reraise


class CompositeHook(AgentHook):
    """Fan out to multiple hooks with per-hook error isolation."""

    def __init__(self, hooks: list[AgentHook], reraise: bool = False) -> None:
        super().__init__(reraise=reraise)
        self._hooks = list(hooks)

    def add(self, hook: AgentHook) -> None:
        self._hooks.append(hook)

    async def _for_each(self, method: str, *args: Any, **kwargs: Any) -> None:
        for hook in self._hooks:
            try:
                await getattr(hook, method)(*args, **kwargs)
            except Exception:
                if hook.reraise:
                    raise
                log.exception("hook %s.%s failed; skipping", type(hook).__name__, method)

    async def before_run(self, context: AgentRunHookContext) -> None:
        await self._for_each("before_run", context)

    async def after_run(self, context: AgentRunHookContext) -> None:
        await self._for_each("after_run", context)

    async def on_error(self, context: AgentRunHookContext) -> None:
        await self._for_each("on_error", context)

    async def on_finally(self, context: AgentRunHookContext) -> None:
        await self._for_each("on_finally", context)

    async def before_iteration(self, context: AgentHookContext) -> None:
        await self._for_each("before_iteration", context)

    async def after_iteration(self, context: AgentHookContext) -> None:
        await self._for_each("after_iteration", context)

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        await self._for_each("before_execute_tools", context)

    async def on_stream(self, context: AgentHookContext, delta: str) -> None:
        await self._for_each("on_stream", context, delta)

    async def on_stream_end(self, context: AgentHookContext, *, resuming: bool = False) -> None:
        await self._for_each("on_stream_end", context, resuming=resuming)

    async def emit_reasoning(self, content: str) -> None:
        await self._for_each("emit_reasoning", content)

    def finalize_content(self, context: AgentHookContext, content: str) -> str | None:
        # Later hooks see the rewritten content of earlier ones.
        current = content
        for hook in self._hooks:
            try:
                rewritten = hook.finalize_content(context, current)
            except Exception:
                if hook.reraise:
                    raise
                log.exception("hook %s.finalize_content failed; skipping", type(hook).__name__)
                continue
            if rewritten is not None:
                current = rewritten
        return current if current != content else None

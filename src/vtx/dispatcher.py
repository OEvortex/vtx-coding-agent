"""Sub-agent dispatcher context.

Generic infrastructure for any tool that wants to dispatch a sub-agent
in-process. The runtime populates a single ``DispatcherContext`` slot
on every relevant state change (initialize, agent change, model
change, thinking-level change); tools that need to spawn sub-agents
read the slot to find the parent's provider, model, cwd, etc.

This module is part of vtx because the runtime is the only thing
that has access to the parent's provider/model/cwd, and the slot is
generic enough to serve any dispatching tool. The actual dispatching
tool (e.g. the bundled example at ``examples/extensions/task_tool.py``)
imports :func:`get_context` from here and uses it to build a sub-agent.

The TUI can optionally install a ``progress_callback`` so a custom
tool block can stream sub-agent events into the parent tool block.
The callback is keyed by ``tool_call_id`` (so multiple in-flight
sub-agents don't collide) and receives a small event dict shape::

    {"kind": "subagent_start" | "text_delta" | "tool_start" |
     "tool_result" | "subagent_end" | "error" | ...,
     "subagent": "<name>", ...}
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class DispatcherContext:
    """Snapshot of the parent's runtime context for sub-agent dispatch.

    Populated by :class:`vtx.runtime.ConversationRuntime` on initialize,
    agent change, model change, and thinking-level change. Read by
    dispatching tools (e.g. the example Task tool) when they need to
    spawn a sub-agent that reuses the parent's provider, model, and
    cwd.
    """

    provider: Any  # vtx.llm.BaseProvider — typed as Any to avoid a hard
    # import (we only read ``.config`` off it).
    model: str
    model_provider: str | None
    base_url: str | None
    thinking_level: str | None
    agent_registry: Any  # vtx.agents.AgentRegistry | None
    cwd: str
    system_prompt: str | None = None
    # Optional callback that the TUI installs to stream sub-agent
    # events into the parent tool block. Signature:
    #   progress_callback(tool_call_id, event_dict) -> None
    progress_callback: Callable[[str, dict], None] | None = None


_context: DispatcherContext | None = None


def set_context(ctx: DispatcherContext | None) -> None:
    """Install (or clear) the dispatcher context used by sub-agent tools.

    The runtime calls this on every relevant state change so the
    context is always fresh. Tools can also call it (e.g. an
    extension's own runtime hook) to install a different
    ``progress_callback`` for the duration of a single tool call.
    """
    global _context
    _context = ctx


def get_context() -> DispatcherContext | None:
    """Return the currently-installed dispatcher context, or None."""
    return _context


__all__ = ["DispatcherContext", "get_context", "set_context"]

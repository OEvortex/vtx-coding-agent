"""Live sub-agent registry for goal runs.

The ``task`` tool streams a small event dict per sub-agent through the
runtime's progress callback (see :meth:`vtx.ai.agent.tools.task.TaskTool`).
The chat log consumes those events to draw each sub-agent's own block, but
the goal beacon had no way to answer the question a user actually asks
mid-goal: *is anything else working on this right now?*

This module is a second, deliberately tiny consumer of the same stream. It
keeps just enough per sub-agent to draw a row, so the goal dashboard can show
that a goal dispatched 3 sub-agents, that 2 are still running, and which
tools they are burning turns on.

State is process-local and best-effort: losing it on reload is harmless.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

#: Terminal statuses a sub-agent can end in. Anything else is "running".
END_STATES = frozenset({"ok", "error", "stopped", "cancelled", "interrupted"})

#: How many sub-agents to keep. A goal run that dispatched more than this is
#: already past the point where the dashboard line is informative.
MAX_TRACKED = 24


@dataclass
class SubagentRun:
    """One dispatched sub-agent, as far as the beacon cares."""

    name: str
    description: str = ""
    model: str | None = None
    max_turns: int | None = None
    turns: int = 0
    tokens: int = 0
    tool_counts: dict[str, int] = field(default_factory=dict)
    active_tool: str | None = None
    last_text: str = ""
    status: str = "running"
    error: str | None = None
    started_at: float = field(default_factory=time.monotonic)
    ended_at: float | None = None

    @property
    def running(self) -> bool:
        return self.status == "running"

    @property
    def elapsed_ms(self) -> float:
        end = self.ended_at if self.ended_at is not None else time.monotonic()
        return max(0.0, (end - self.started_at) * 1000)

    def tool_uses(self) -> int:
        return sum(self.tool_counts.values())

    def top_tools(self, limit: int = 2) -> str:
        """`read×3 grep×1`-style summary of where turns went."""  # noqa: RUF002
        if not self.tool_counts:
            return ""
        ranked = sorted(self.tool_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        return " ".join(f"{name}×{count}" for name, count in ranked[:limit])  # noqa: RUF001


class SubagentRegistry:
    """Ordered, bounded store of sub-agent runs for the current goal."""

    def __init__(self, max_tracked: int = MAX_TRACKED) -> None:
        self._runs: dict[str, SubagentRun] = {}
        self._order: list[str] = []
        self._max = max_tracked

    def record(self, event: dict) -> SubagentRun | None:
        """Fold one Task-tool progress event into the registry.

        Returns the affected run, or ``None`` for events that carry no
        sub-agent identity. Safe to call with any dict — malformed events are
        ignored rather than raised, because a broken beacon must not be able
        to break the turn that feeds it.
        """
        if not isinstance(event, dict):
            return None
        name = str(event.get("subagent") or "").strip()
        kind = str(event.get("kind") or "")
        if not kind:
            return None
        if not name:
            name = "subagent"

        run = self._runs.get(name)
        if run is None:
            run = SubagentRun(name=name)
            self._runs[name] = run
            self._order.append(name)
            self._evict()

        if kind == "subagent_start":
            run.description = str(event.get("description") or "")
            run.model = event.get("model")
            run.max_turns = event.get("max_turns")
            run.status = "running"
            run.started_at = time.monotonic()
        elif kind == "text_delta":
            run.last_text = (run.last_text + str(event.get("delta") or ""))[-400:]
        elif kind == "tool_start":
            run.active_tool = event.get("tool_name")
        elif kind == "tool_result":
            run.active_tool = None
        elif kind == "turn_end":
            run.turns = int(event.get("turns") or run.turns)
            run.tokens = int(event.get("tokens") or run.tokens)
        elif kind == "error":
            run.error = str(event.get("error") or "error")
        elif kind in ("interrupted", "cancelled"):
            run.error = run.error or kind
            self._finish(run, kind)
        elif kind == "subagent_end":
            # The only event guaranteed to carry the final counters, so it
            # both records them and closes the run out.
            run.turns = int(event.get("turns") or run.turns)
            run.tokens = int(event.get("tokens") or run.tokens)
            self._finish(run, "ok")

        if event.get("tool_counts"):
            counts = event["tool_counts"]
            if isinstance(counts, dict):
                run.tool_counts = {str(k): int(v) for k, v in counts.items()}
        return run

    @staticmethod
    def _finish(run: SubagentRun, status: str) -> None:
        if run.status in END_STATES:
            return
        run.status = "error" if run.error else status
        run.ended_at = time.monotonic()
        run.active_tool = None

    def _evict(self) -> None:
        """Drop the oldest *finished* runs once over the cap.

        Running agents are never evicted — a live sub-agent with no row is
        worse than a slightly stale registry. If everything is still running
        we simply let the registry grow past the cap.
        """
        for name in list(self._order):
            if len(self._order) <= self._max:
                return
            run = self._runs.get(name)
            if run is not None and run.running:
                continue
            self._order.remove(name)
            self._runs.pop(name, None)

    def runs(self) -> list[SubagentRun]:
        """Tracked runs, oldest first (callers sort by status themselves)."""
        return [self._runs[key] for key in self._order if key in self._runs]

    def counts(self) -> tuple[int, int]:
        """``(running, finished)``."""
        all_runs = self.runs()
        running = sum(1 for r in all_runs if r.running)
        return running, len(all_runs) - running

    def total_tokens(self) -> int:
        return sum(r.tokens for r in self._runs.values())

    def clear(self) -> None:
        self._runs.clear()
        self._order.clear()

    def __bool__(self) -> bool:
        return bool(self._runs)


#: Process-wide registry. One app process == one chat == one goal run at a
#: time, so a module singleton keeps the wiring trivial.
REGISTRY = SubagentRegistry()


def record_subagent_event(event: dict) -> None:
    """Fold a Task-tool progress event into the global registry."""
    REGISTRY.record(event)


def reset_subagents() -> None:
    """Drop all tracked sub-agents (start of a new goal run / new turn)."""
    REGISTRY.clear()

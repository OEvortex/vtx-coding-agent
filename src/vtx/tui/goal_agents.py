"""Live sub-agent registry.

The ``task`` tool streams a small event dict per sub-agent through the
runtime's progress callback (see :meth:`vtx.ai.agent.tools.task.TaskTool`).
Two consumers fold those events in: the chat log (which draws the tool block
for the dispatch) and this registry, which keeps just enough per sub-agent to
answer the question a user actually asks mid-run: *what is working on this
right now, and what is still waiting?*

Runs are keyed by the dispatch's ``tool_call_id``, not by sub-agent name, so
four concurrent ``Explore`` runs are four rows rather than one smeared row.
State is process-local and best-effort: losing it on reload is harmless.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

#: Terminal statuses a sub-agent can end in. Anything else is in flight.
END_STATES = frozenset({"ok", "error", "stopped", "cancelled", "interrupted"})

#: How many sub-agents to keep. A turn that dispatched more than this is
#: already past the point where the list is informative, and the oldest
#: finished rows are evicted first.
MAX_TRACKED = 48

#: How long a finished run stays worth showing once nothing else is in flight.
DONE_LINGER_SECONDS = 8.0


#: How long a finished run stays worth keeping once a new turn starts. Long
#: enough that a sub-agent which just landed is still visible on the next
#: screen, short enough that the strip is never a session log.
FINISHED_TTL_SECONDS = 180.0


@dataclass
class SubagentRun:
    """One dispatched sub-agent."""

    run_id: str
    name: str
    description: str = ""
    model: str | None = None
    max_turns: int | None = None
    turns: int = 0
    tokens: int = 0
    tool_counts: dict[str, int] = field(default_factory=dict)
    active_tool: str | None = None
    last_text: str = ""
    status: str = "queued"
    error: str | None = None
    #: FIFO position while queued; 1 means "next to start".
    queue_position: int = 0
    dispatched_at: float = field(default_factory=time.monotonic)
    started_at: float | None = None
    ended_at: float | None = None

    @property
    def running(self) -> bool:
        return self.status == "running"

    @property
    def queued(self) -> bool:
        return self.status == "queued"

    @property
    def finished(self) -> bool:
        return self.status in END_STATES

    @property
    def elapsed_ms(self) -> float:
        """Wall time since the run started (or ended, once it is over)."""
        if self.started_at is None:
            return 0.0
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
    """Ordered, bounded store of sub-agent runs."""

    def __init__(self, max_tracked: int = MAX_TRACKED) -> None:
        self._runs: dict[str, SubagentRun] = {}
        self._order: list[str] = []
        self._max = max_tracked

    def record(self, run_id: str, event: dict) -> SubagentRun | None:
        """Fold one Task-tool progress event into the registry.

        ``run_id`` is the dispatch's tool call id, so identical sub-agent
        names stay distinct. Returns the affected run, or ``None`` for events
        that carry no sub-agent identity. Safe to call with any dict —
        malformed events are ignored rather than raised, because a broken
        panel must not be able to break the turn that feeds it.
        """
        if not isinstance(event, dict):
            return None
        kind = str(event.get("kind") or "")
        if not kind:
            return None
        key = (run_id or "").strip() or str(event.get("subagent") or "").strip() or "subagent"

        run = self._runs.get(key)
        if run is None:
            run = SubagentRun(run_id=key, name=str(event.get("subagent") or "subagent"))
            self._runs[key] = run
            self._order.append(key)
            self._evict()

        if kind == "subagent_queued":
            run.description = str(event.get("description") or run.description)
            run.model = event.get("model") or run.model
            run.max_turns = event.get("max_turns") or run.max_turns
            run.queue_position = int(event.get("position") or 0)
            run.status = "queued"
        elif kind == "subagent_start":
            run.description = str(event.get("description") or "")
            run.model = event.get("model")
            run.max_turns = event.get("max_turns")
            run.status = "running"
            run.queue_position = 0
            run.started_at = time.monotonic()
        elif kind == "text_delta":
            run.last_text = (run.last_text + str(event.get("delta") or ""))[-400:]
        elif kind == "tool_start":
            run.active_tool = event.get("tool_name")
            if not event.get("tool_counts"):
                # Emitters that don't ship the full tally still deserve a count.
                tool_name = str(event.get("tool_name") or "")
                if tool_name:
                    run.tool_counts[tool_name] = run.tool_counts.get(tool_name, 0) + 1
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

    def forget(self, run_id: str) -> None:
        """Drop one run (a dispatch the model retried, say)."""
        if run_id in self._runs:
            self._order.remove(run_id)
            self._runs.pop(run_id, None)

    @staticmethod
    def _finish(run: SubagentRun, status: str) -> None:
        if run.finished:
            return
        run.status = "error" if run.error else status
        run.ended_at = time.monotonic()
        run.active_tool = None
        run.queue_position = 0
        # A run cancelled before it ever started has no start time to measure
        # from; borrow dispatch time so the row still shows a duration.
        if run.started_at is None:
            run.started_at = run.ended_at

    def _evict(self) -> None:
        """Drop the oldest *finished* runs once over the cap.

        Queued and running agents are never evicted — a live sub-agent with no
        row is worse than a slightly stale registry. If everything is still
        in flight we simply let the registry grow past the cap.
        """
        for key in list(self._order):
            if len(self._order) <= self._max:
                return
            run = self._runs.get(key)
            if run is not None and not run.finished:
                continue
            self._order.remove(key)
            self._runs.pop(key, None)

    def runs(self) -> list[SubagentRun]:
        """Tracked runs, oldest dispatch first (callers sort by status)."""
        return [self._runs[key] for key in self._order if key in self._runs]

    def prune_finished(
        self, max_age: float = FINISHED_TTL_SECONDS, *, now: float | None = None
    ) -> int:
        """Forget runs that ended longer ago than ``max_age``. Returns the count.

        Only finished runs age out. A live sub-agent is never pruned, however
        old its dispatch was.
        """
        now = time.monotonic() if now is None else now
        dropped = 0
        for key in list(self._order):
            run = self._runs.get(key)
            if run is None or not run.finished or run.ended_at is None:
                continue
            if now - run.ended_at > max_age:
                self._order.remove(key)
                self._runs.pop(key, None)
                dropped += 1
        return dropped

    def counts(self) -> tuple[int, int, int]:
        """``(running, queued, finished)``."""
        all_runs = self.runs()
        running = sum(1 for r in all_runs if r.running)
        queued = sum(1 for r in all_runs if r.queued)
        return running, queued, len(all_runs) - running - queued

    def has_live(self) -> bool:
        """True while any sub-agent is queued or running."""
        return any(not r.finished for r in self._runs.values())

    def total_tokens(self) -> int:
        return sum(r.tokens for r in self._runs.values())

    def clear(self) -> None:
        self._runs.clear()
        self._order.clear()

    def __bool__(self) -> bool:
        return bool(self._runs)


#: Process-wide registry. One app process == one chat, so a module singleton
#: keeps the wiring trivial.
REGISTRY = SubagentRegistry()


def record_subagent_event(run_id: str, event: dict) -> None:
    """Fold a Task-tool progress event into the global registry."""
    REGISTRY.record(run_id, event)


def prune_finished_subagents(max_age: float = FINISHED_TTL_SECONDS) -> None:
    """Turn-boundary housekeeping: forget sub-agents that ended long ago.

    This replaced a blanket reset at the start of every run. A reset looked
    tidy — the strip only ever described the work in flight — but it erased
    background sub-agents the moment the user sent their next message, which
    is exactly the kind that outlives the turn that dispatched them.
    """
    REGISTRY.prune_finished(max_age)

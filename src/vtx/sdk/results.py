"""RunResult — the value returned by ``Runner.run_sync`` / ``Runner.run``."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..core.types import StopReason, Usage
from .items import RunItem


@dataclass
class RunResult:
    """The result of a completed (or interrupted) run."""

    final_output: Any
    """The final output of the run. Usually a string, but ``Pydantic`` models
    are supported via ``Agent(output_type=...)``."""

    new_items: list[RunItem] = field(default_factory=list)
    """All ``RunItem`` objects produced during the run, in order."""

    interruptions: list[Any] = field(default_factory=list)
    """``ToolApprovalItem`` objects that need a decision before the run can
    continue. Empty for runs that finished without pausing for approval."""

    state: Any | None = None
    """A :class:`RunState` snapshot. ``None`` unless the run paused for
    approval; you can pass it back to ``Runner.run`` to resume."""

    stop_reason: StopReason = StopReason.STOP
    """The reason the run stopped (``STOP``, ``TOOL_USE``, ``LENGTH``,
    ``ERROR``, ``INTERRUPTED``)."""

    usage: Usage | None = None
    """Token usage across the run, aggregated from every turn."""

    agent_name: str = ""
    """The name of the agent that produced the final output."""

    _streamed_events: list[Any] = field(  # type: ignore[assignment]
        default_factory=list, repr=False, compare=False
    )
    """Internal: events yielded during a streamed run, used by
    :meth:`Runner.run_streamed`. Not part of the public API."""

    def to_input_list(self) -> list[dict[str, Any]]:
        """Return the full conversation as a list of input-item dicts.

        Mirrors OpenAI's ``RunResult.to_input_list()``. The list can be
        fed to a subsequent ``Runner.run(..., input=...)`` call to
        continue the conversation manually.
        """
        items: list[dict[str, Any]] = []
        for item in self.new_items:
            items.append(item.to_input_item())
        return items

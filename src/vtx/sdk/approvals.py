"""Approvals: human-in-the-loop and resumable run state."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, TypeVar

from ..core.types import ToolCall
from .items_base import RunItemBase

T = TypeVar("T")


class ApprovalDecision(StrEnum):
    """The user's decision for a pending tool call."""

    APPROVE = "approve"
    REJECT = "reject"


@dataclass
class ToolApprovalItem(RunItemBase[Any]):
    """A pending tool call waiting on user approval.

    The ``raw_item`` is the :class:`vtx.core.types.ToolCall` that the
    model emitted. The :class:`RunState` queues an :class:`ApprovalDecision`
    for each of these before the run is resumed.
    """

    tool_name: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    type: str = "tool_approval_item"

    @property
    def call_id(self) -> str:
        if isinstance(self.raw_item, ToolCall):
            return self.raw_item.id
        return ""

    @property
    def name(self) -> str:
        return self.tool_name

    def to_input_item(self) -> dict[str, Any]:
        # ToolApprovalItem should never be re-sent to the model. Callers
        # should filter these out of input lists.
        raise RuntimeError(
            "ToolApprovalItem cannot be converted to an input item; "
            "filter it out before sending to the model."
        )


@dataclass
class _PendingDecision:
    """Queued decision for a pending approval."""

    call_id: str
    decision: ApprovalDecision


@dataclass
class RunState[T]:
    """Resumable state for a run that paused for approval.

    Pass this object back to :func:`Runner.run` (or its variants) to
    continue the run after decisions have been queued.
    """

    original_input: Any = None
    """The user input that started the run."""

    pending_tool_calls: list[ToolCall] = field(default_factory=list)
    """Tool calls that need a decision before the run can continue."""

    decisions: list[_PendingDecision] = field(default_factory=list)
    """Decisions queued via :meth:`approve` / :meth:`reject`."""

    new_items: list[Any] = field(default_factory=list)
    """Items generated so far (for inspection)."""

    metadata: dict[str, Any] = field(default_factory=dict)

    def approve(self, *items: ToolApprovalItem) -> None:
        """Approve one or more pending tool calls."""
        for item in items:
            self.decisions.append(
                _PendingDecision(call_id=item.call_id, decision=ApprovalDecision.APPROVE)
            )

    def reject(self, *items: ToolApprovalItem) -> None:
        """Reject one or more pending tool calls."""
        for item in items:
            self.decisions.append(
                _PendingDecision(call_id=item.call_id, decision=ApprovalDecision.REJECT)
            )

    def decision_for(self, call_id: str) -> ApprovalDecision | None:
        """Return the queued decision for ``call_id`` and clear it."""
        for i, decision in enumerate(self.decisions):
            if decision.call_id == call_id:
                self.decisions.pop(i)
                return decision.decision
        return None

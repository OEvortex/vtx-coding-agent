"""Tests for approvals and RunState."""

from __future__ import annotations

import pytest

from vtx.core.types import ToolCall
from vtx.sdk.approvals import ApprovalDecision, RunState, ToolApprovalItem


def _make_approval(
    call_id: str, tool_name: str = "t", arguments: dict | None = None
) -> ToolApprovalItem:
    return ToolApprovalItem(
        agent=None,
        raw_item=ToolCall(id=call_id, name=tool_name, arguments=arguments or {}),
        tool_name=tool_name,
        arguments=arguments or {},
    )


def test_approval_decision_values() -> None:
    assert ApprovalDecision.APPROVE.value == "approve"
    assert ApprovalDecision.REJECT.value == "reject"


def test_tool_approval_item_call_id_and_name() -> None:
    item = _make_approval("call_1", "my_tool", {"x": 1})
    assert item.call_id == "call_1"
    assert item.name == "my_tool"
    assert item.arguments == {"x": 1}


def test_tool_approval_item_to_input_raises() -> None:
    item = _make_approval("call_1")
    with pytest.raises(RuntimeError):
        item.to_input_item()


def test_run_state_initial() -> None:
    state = RunState()
    assert state.pending_tool_calls == []
    assert state.decisions == []


def test_run_state_approve() -> None:
    state = RunState()
    state.approve(_make_approval("c1"), _make_approval("c2"))
    assert len(state.decisions) == 2
    assert state.decisions[0].decision == ApprovalDecision.APPROVE
    assert state.decisions[1].decision == ApprovalDecision.APPROVE


def test_run_state_reject() -> None:
    state = RunState()
    state.reject(_make_approval("c1"))
    assert state.decisions[0].decision == ApprovalDecision.REJECT


def test_run_state_decision_for_pops() -> None:
    state = RunState()
    state.approve(_make_approval("c1"))
    state.approve(_make_approval("c2"))
    d = state.decision_for("c1")
    assert d == ApprovalDecision.APPROVE
    # c1 is gone; calling again returns None.
    assert state.decision_for("c1") is None
    # c2 is still there.
    assert state.decision_for("c2") == ApprovalDecision.APPROVE

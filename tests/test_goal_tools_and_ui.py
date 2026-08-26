"""Tests for goal UI rendering, widget behaviour, and the single goal tool."""

from __future__ import annotations

from pathlib import Path

import pytest

from vtx.ai.agent.dispatcher import DispatcherContext, set_context
from vtx.coding_agent.goal import storage
from vtx.coding_agent.goal.service import GoalService
from vtx.coding_agent.goal.tools import GoalParams, GoalTool
from vtx.tui.goal_ui import format_usage, render_compact, render_expanded


@pytest.fixture()
def goal_cwd(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _install_dispatcher(cwd: Path) -> None:
    set_context(
        DispatcherContext(
            provider=None,
            model="test-model",
            model_provider=None,
            base_url=None,
            thinking_level="high",
            agent_registry=None,
            cwd=str(cwd),
        )
    )


# ---------------------------------------------------------------------------
# pure renderers
# ---------------------------------------------------------------------------


def test_render_compact_and_expanded(goal_cwd: Path) -> None:
    service = GoalService(str(goal_cwd))
    record = service.create(
        "Add CSV export to reports", verification="Run npm test with zero failures."
    )
    service.replace_tasks(
        record.id,
        [
            {"title": "Review reports page"},
            {"title": "Implement export", "note": "Contract: CSV matches filters"},
            {"title": "Add docs"},
        ],
    )
    service.update_task(record.id, "t1", "complete", evidence="page reviewed")
    service.update_task(record.id, "t2", "start")

    record = service.focused()
    compact = render_compact(service, record)
    text = compact.plain
    assert "vtx-goal" in text
    assert "running" in text
    assert "▸ t2" in text
    assert "✓ t1" in text
    assert "Verify" in text

    expanded = render_expanded(service, record).plain
    assert "Progress" in expanded
    assert "Current task" in expanded
    assert "CSV matches filters" in expanded  # per-task contract surfaced
    assert "Verification" in expanded


def test_format_usage_hours_and_budget() -> None:
    from vtx.coding_agent.goal.record import GoalRecord, GoalUsage

    record = GoalRecord(id="x", mode="regular", status="active", objective="o")
    record.usage = GoalUsage(input_tokens=18_200, output_tokens=0, elapsed_ms=767_000)
    formatted = format_usage(record)
    assert "12m47s" in formatted
    assert "18.2K" in formatted
    record.token_budget = 100_000
    assert "/100K" in format_usage(record)


def test_format_usage_empty_hides_display() -> None:
    from vtx.coding_agent.goal.record import GoalRecord

    record = GoalRecord(id="x", mode="regular", status="active", objective="o")
    assert format_usage(record) == ""


# ---------------------------------------------------------------------------
# single goal tool end-to-end (auditor disabled so no provider is needed)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_goal_tool_full_lifecycle_archives_without_audit(goal_cwd: Path) -> None:
    _install_dispatcher(goal_cwd)
    from vtx.coding_agent.goal.service import get_service

    service = get_service(str(goal_cwd))
    service.focused_id = None
    service.update_settings(auditorEnabled=False)

    created = await GoalTool().execute(
        GoalParams(action="create", objective="Ship it", verification="tests green")
    )
    assert created.success
    record = service.focused()
    assert record is not None

    snapshot = await GoalTool().execute(GoalParams(action="get"))
    assert snapshot.success and "Ship it" in snapshot.result

    tasks_result = await GoalTool().execute(
        GoalParams(action="set_tasks", tasks=[{"title": "step one"}, {"title": "step two"}])
    )
    assert tasks_result.success

    start = await GoalTool().execute(
        GoalParams(action="update_task", task_id="t1", task_status="start")
    )
    assert start.success
    done = await GoalTool().execute(
        GoalParams(
            action="update_task", task_id="t1", task_status="complete", evidence="done deal"
        )
    )
    assert done.success

    # Completing while the auditor is disabled archives the goal explicitly.
    finished = await GoalTool().execute(
        GoalParams(action="update", status="complete", completion_summary="all steps verified")
    )
    assert finished.success
    assert "NOT independently approved" in finished.result
    assert service.pool() == {}
    ledger_types = {e["type"] for e in storage.read_ledger(str(goal_cwd), limit_last=100)}
    assert "audit_skipped" in ledger_types


@pytest.mark.asyncio
async def test_goal_tool_rejects_unknown_action_and_missing_args(goal_cwd: Path) -> None:
    _install_dispatcher(goal_cwd)
    from vtx.coding_agent.goal.service import get_service

    get_service(str(goal_cwd)).focused_id = None
    bad_action = await GoalTool().execute(GoalParams(action="explode"))
    assert not bad_action.success

    missing_objective = await GoalTool().execute(GoalParams(action="create"))
    assert not missing_objective.success


@pytest.mark.asyncio
async def test_goal_tool_blocked_when_disabled(goal_cwd: Path) -> None:
    _install_dispatcher(goal_cwd)
    from vtx.coding_agent.goal.service import get_service

    service = get_service(str(goal_cwd))
    service.focused_id = None
    service.update_settings(disabled=True)
    result = await GoalTool().execute(GoalParams(action="create", objective="nope"))
    assert not result.success


@pytest.mark.asyncio
async def test_goal_tool_conflict_when_already_focused(goal_cwd: Path) -> None:
    _install_dispatcher(goal_cwd)
    from vtx.coding_agent.goal.service import get_service

    service = get_service(str(goal_cwd))
    service.focused_id = None
    service.create("existing goal")
    conflict = await GoalTool().execute(GoalParams(action="create", objective="second"))
    assert not conflict.success
    assert "already focuses" in conflict.result


@pytest.mark.asyncio
async def test_goal_tool_complete_does_not_pause_goal(goal_cwd: Path) -> None:
    _install_dispatcher(goal_cwd)
    from vtx.coding_agent.goal.service import get_service

    service = get_service(str(goal_cwd))
    service.focused_id = None
    service.update_settings(auditorEnabled=False)

    created = await GoalTool().execute(
        GoalParams(action="create", objective="Test completion", verification="pass")
    )
    assert created.success
    record = service.focused()
    assert record is not None
    assert record.status == "active"

    # Complete goal
    res = await GoalTool().execute(
        GoalParams(action="update", status="complete", completion_summary="done")
    )
    assert res.success
    # Archived record should have status="complete", not "paused"
    archived = service._read_any(record.id)
    assert archived is not None
    assert archived.status == "complete"
    assert archived.paused_reason is None

"""Tests for goal UI rendering, widget behaviour, and the single goal tool."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.cells import cell_len

from vtx.ai.agent.dispatcher import DispatcherContext, set_context
from vtx.coding_agent.goal import storage
from vtx.coding_agent.goal.service import GoalService
from vtx.coding_agent.goal.tools import GoalParams, GoalTool
from vtx.tui.goal_ui import format_usage, render_compact, render_expanded


@pytest.fixture()
def goal_cwd(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _install_dispatcher(cwd: Path, session_id: str = "") -> None:
    set_context(
        DispatcherContext(
            provider=None,
            model="test-model",
            model_provider=None,
            base_url=None,
            thinking_level="high",
            agent_registry=None,
            cwd=str(cwd),
            session=SimpleNamespace(id=session_id) if session_id else None,
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


def test_format_usage_scales_to_millions() -> None:
    """A long goal must not read as `27631.9K`."""
    from vtx.coding_agent.goal.record import GoalRecord, GoalUsage

    record = GoalRecord(id="x", mode="regular", status="active", objective="o")
    record.usage = GoalUsage(input_tokens=27_631_900, output_tokens=0, elapsed_ms=3_403_000)
    formatted = format_usage(record)
    assert "27.6M" in formatted
    assert "K" not in formatted


# ---------------------------------------------------------------------------
# width-aware layout
# ---------------------------------------------------------------------------


def _line_widths(text) -> list[int]:
    return [cell_len(line) for line in text.plain.split("\n") if line]


@pytest.mark.parametrize("width", [44, 52, 60, 80, 100, 120])
def test_compact_rows_are_exactly_the_widget_width(goal_cwd: Path, width: int) -> None:
    """Every box row must be exactly `width` cells — no ragged right edge."""
    service = GoalService(str(goal_cwd))
    record = service.create(
        "把目标系统现代化 so the beacon never truncates the auditor verdict",
        verification="pytest -k goal && ruff check .",
    )
    service.replace_tasks(record.id, [{"title": "First task"}, {"title": "Second task"}])
    service.update_task(record.id, "t1", "start")
    record = service.focused()

    text = render_compact(service, record, width=width)
    assert set(_line_widths(text)) == {width}, f"ragged rows at width={width}"


def test_compact_degrades_below_min_width(goal_cwd: Path) -> None:
    """Too narrow for the box: one status line, no chrome, still fits."""
    service = GoalService(str(goal_cwd))
    record = service.create("Ship the thing")
    record = service.focused()

    text = render_compact(service, record, width=24)
    assert "╭" not in text.plain
    assert "╰" not in text.plain
    assert "running" in text.plain
    assert max(_line_widths(text)) <= 24


def test_compact_never_exceeds_width_with_wide_glyphs(goal_cwd: Path) -> None:
    """Double-width CJK must not shear the borders (cell_len, not len)."""
    service = GoalService(str(goal_cwd))
    record = service.create("这是一个非常长的目标描述" * 4, verification="测试命令 && echo ok")
    record = service.focused()

    text = render_compact(service, record, width=64)
    assert set(_line_widths(text)) == {64}


def test_verification_wraps_instead_of_being_clipped(goal_cwd: Path) -> None:
    """A long verification command stays readable across lines."""
    service = GoalService(str(goal_cwd))
    record = service.create(
        "Ship it", verification=" && ".join(f"pytest -k case_{i}" for i in range(12))
    )
    record = service.focused()

    text = render_compact(service, record, width=80)
    assert set(_line_widths(text)) == {80}
    # The tail of the command survives instead of being cut at 48 chars.
    assert "case_11" in text.plain


# ---------------------------------------------------------------------------
# sub-agent visibility
# ---------------------------------------------------------------------------


def test_compact_shows_spawned_subagents(goal_cwd: Path) -> None:
    from vtx.tui.goal_agents import REGISTRY

    REGISTRY.clear()
    try:
        service = GoalService(str(goal_cwd))
        record = service.create("Ship the thing")
        service.replace_tasks(record.id, [{"title": "Do the work"}])
        service.update_task(record.id, "t1", "start")
        record = service.focused()

        assert "reviewer" not in render_compact(service, record, width=80).plain

        REGISTRY.record("r1", {"kind": "subagent_start", "subagent": "reviewer", "max_turns": 20})
        REGISTRY.record("r1", {"kind": "tool_start", "subagent": "reviewer", "tool_name": "read"})
        REGISTRY.record("r2", {"kind": "subagent_queued", "subagent": "tester", "position": 1})

        text = render_compact(service, record, width=80).plain
        assert "reviewer" in text
        assert "1 queued" in text
        assert set(_line_widths(render_compact(service, record, width=80))) == {80}
    finally:
        REGISTRY.clear()


def test_expanded_lists_subagent_stats(goal_cwd: Path) -> None:
    from vtx.tui.goal_agents import REGISTRY

    REGISTRY.clear()
    try:
        service = GoalService(str(goal_cwd))
        record = service.create("Ship the thing")
        record = service.focused()

        REGISTRY.record(
            "r1",
            {
                "kind": "subagent_start",
                "subagent": "code-reviewer",
                "model": "claude-sonnet-5",
                "max_turns": 30,
            },
        )
        REGISTRY.record(
            "r1",
            {
                "kind": "subagent_end",
                "subagent": "code-reviewer",
                "turns": 4,
                "tokens": 38_210,
                "tool_counts": {"read": 9, "grep": 4},
            },
        )

        text = render_expanded(service, record, width=100).plain
        assert "Agents" in text
        assert "code-reviewer" in text
        assert "↻4≤30" in text
        assert "38.2K tok" in text
    finally:
        REGISTRY.clear()


def test_subagent_registry_never_evicts_running_agents() -> None:
    from vtx.tui.goal_agents import SubagentRegistry

    registry = SubagentRegistry(max_tracked=2)
    registry.record("a", {"kind": "subagent_start", "subagent": "a"})
    registry.record("b", {"kind": "subagent_start", "subagent": "b"})
    registry.record("c", {"kind": "subagent_start", "subagent": "c"})
    # All three are still running, so nothing may be dropped.
    assert len(registry.runs()) == 3
    assert registry.counts() == (3, 0, 0)

    registry.record("a", {"kind": "subagent_end", "subagent": "a", "turns": 1})
    registry.record("d", {"kind": "subagent_start", "subagent": "d"})
    # `a` finished, so it is the one that gets evicted.
    assert [r.name for r in registry.runs()].count("a") == 0
    assert {r.name for r in registry.runs()} >= {"b", "c", "d"}


def test_subagent_registry_keeps_same_named_runs_apart() -> None:
    """Four `Explore` fan-outs are four rows, not one smeared row."""
    from vtx.tui.goal_agents import SubagentRegistry

    registry = SubagentRegistry()
    for i in range(4):
        registry.record(f"call_{i}", {"kind": "subagent_start", "subagent": "Explore"})
    assert len(registry.runs()) == 4
    assert {r.run_id for r in registry.runs()} == {f"call_{i}" for i in range(4)}

    registry.record("call_2", {"kind": "subagent_end", "subagent": "Explore", "turns": 2})
    assert registry.counts() == (3, 0, 1)


def test_subagent_registry_ignores_malformed_events() -> None:
    from vtx.tui.goal_agents import SubagentRegistry

    registry = SubagentRegistry()
    assert registry.record("x", {}) is None
    assert registry.record("x", {"kind": "subagent_start"}) is not None
    assert not registry.record("x", None)
    assert registry.runs()[0].name == "subagent"


# ---------------------------------------------------------------------------
# auditor feedback must survive intact (regression: the audit loop)
# ---------------------------------------------------------------------------


def test_long_auditor_feedback_is_stored_whole(goal_cwd: Path) -> None:
    """The 1500-char cut made a failed audit impossible to act on.

    The verdict was clipped mid-sentence on write, so the agent could not
    read the rest and the goal re-audited forever. Long feedback must now be
    stored verbatim.
    """
    service = GoalService(str(goal_cwd))
    record = service.create("Ship the thing")

    long_feedback = "## Audit findings\n\n" + ("A finding that must not be lost. " * 200)
    assert len(long_feedback) > 4000, "fixture must exceed the old cap"

    service.set_status(record.id, "active", review_feedback=long_feedback)
    reloaded = service.focused()
    assert reloaded is not None
    assert reloaded.review_feedback == long_feedback


def test_snapshot_returns_full_objective_and_feedback(goal_cwd: Path) -> None:
    """`goal(action="get")` is the agent's only view; it must not clip."""
    from vtx.coding_agent.goal.tools import _snapshot_text

    service = GoalService(str(goal_cwd))
    objective = "Do the thing. " * 100  # > the old 200-char cut
    record = service.create(objective)
    feedback = "Detailed finding. " * 200
    service.set_status(record.id, "active", review_feedback=feedback)

    reloaded = service.focused()
    assert reloaded is not None
    snapshot = _snapshot_text(reloaded, service)

    assert objective.strip() in snapshot
    assert feedback in snapshot
    assert "…" not in snapshot.split("Objective:")[1].split("\n")[0]


def test_short_clips_on_a_boundary_and_marks_the_clip() -> None:
    """Ledger-size summaries clip on a boundary and say that they did."""
    from vtx.coding_agent.goal.tools import CLIPPED, _short

    text = "First paragraph.\n\nSecond paragraph is much longer than the limit here.\n\nThird."
    clipped = _short(text, 60)
    assert CLIPPED in clipped
    assert clipped.startswith("First paragraph.")
    # Never ends mid-word on a hard cut.
    assert not _short("y" * 200, 40).rstrip(CLIPPED).endswith(("the", "and"))


def test_truncate_on_words_measures_cells_and_marks_cuts() -> None:
    from vtx.coding_agent.goal.record import truncate_on_words

    # Word boundary, not mid-token.
    assert (
        truncate_on_words("All four phases shipped and the verification contract", 40)
        == "All four phases shipped and the…"
    )
    # Always marked, even for a single over-long token.
    assert truncate_on_words("x" * 100, 20).endswith("…")
    assert cell_len(truncate_on_words("x" * 100, 20)) == 20
    # Double-width characters counted as two cells. A wide glyph can leave a
    # cell unused rather than being split, so the bound is `<=`, never `>`.
    assert cell_len(truncate_on_words("这是一个非常长的目标描述文本", 20)) <= 20
    assert truncate_on_words("short", 40) == "short"


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


@pytest.mark.asyncio
async def test_goal_tool_is_scoped_to_the_calling_session(goal_cwd: Path) -> None:
    _install_dispatcher(goal_cwd, "session-a")
    from vtx.coding_agent.goal.service import get_service

    get_service(str(goal_cwd), "session-a").focused_id = None
    created = await GoalTool().execute(GoalParams(action="create", objective="mine only"))
    assert created.success
    goal_id = get_service(str(goal_cwd), "session-a").focused().id

    # A second instance in the same project sees nothing at all.
    _install_dispatcher(goal_cwd, "session-b")
    other = get_service(str(goal_cwd), "session-b")
    other.focused_id = None
    assert other.pool() == {}

    blocked = await GoalTool().execute(GoalParams(action="create", objective="theirs"))
    assert blocked.success, "the other session has no goal in the way"
    assert other.focused().session_id == "session-b"

    # ...and the first session's goal is still untouched and reachable.
    _install_dispatcher(goal_cwd, "session-a")
    mine = get_service(str(goal_cwd), "session-a")
    assert list(mine.pool()) == [goal_id]
    assert mine.get(goal_id).objective == "mine only"


@pytest.mark.asyncio
async def test_goal_tool_list_orphans_and_claim(goal_cwd: Path) -> None:
    _install_dispatcher(goal_cwd, "session-a")
    from vtx.coding_agent.goal.service import get_service

    service_a = get_service(str(goal_cwd), "session-a")
    service_a.focused_id = None
    await GoalTool().execute(GoalParams(action="create", objective="left behind"))
    goal_id = service_a.focused().id
    # session-a exits without a clean unmount: the lease goes stale.
    storage.release_lease(str(goal_cwd), "session-a")

    _install_dispatcher(goal_cwd, "session-b")
    listed = await GoalTool().execute(GoalParams(action="list_orphans"))
    assert listed.success
    assert goal_id in listed.result
    assert "left behind" in listed.result

    no_id = await GoalTool().execute(GoalParams(action="claim"))
    assert not no_id.success
    assert goal_id in no_id.result

    claimed = await GoalTool().execute(GoalParams(action="claim", goal_id=goal_id))
    assert claimed.success
    service_b = get_service(str(goal_cwd), "session-b")
    assert service_b.focused().id == goal_id
    assert service_b.focused().session_id == "session-b"

    # Now that b owns it, it is no longer an orphan and a is locked out.
    _install_dispatcher(goal_cwd, "session-a")
    assert get_service(str(goal_cwd), "session-a").pool() == {}
    assert (await GoalTool().execute(GoalParams(action="list_orphans"))).success
    assert goal_id not in (await GoalTool().execute(GoalParams(action="list_orphans"))).result


@pytest.mark.asyncio
async def test_goal_tool_claim_rejected_while_owner_is_live(goal_cwd: Path) -> None:
    _install_dispatcher(goal_cwd, "session-a")
    from vtx.coding_agent.goal.service import get_service

    service_a = get_service(str(goal_cwd), "session-a")
    service_a.focused_id = None
    await GoalTool().execute(GoalParams(action="create", objective="busy goal"))
    goal_id = service_a.focused().id
    service_a.pool()  # renew session-a's lease

    _install_dispatcher(goal_cwd, "session-b")
    result = await GoalTool().execute(GoalParams(action="claim", goal_id=goal_id))
    assert not result.success
    assert "another running vtx session" in result.result


@pytest.mark.asyncio
async def test_run_completion_audit_verdict_parsing(goal_cwd: Path) -> None:
    from unittest.mock import MagicMock

    from vtx.ai.base import ProviderConfig
    from vtx.coding_agent.goal.auditor import run_completion_audit
    from vtx.coding_agent.goal.record import GoalRecord
    from vtx.core import TurnEndEvent
    from vtx.core.types import AssistantMessage, TextContent

    record = GoalRecord(id="test-goal", mode="regular", status="active", objective="Fix bug")
    mock_provider = MagicMock()
    mock_provider.config = ProviderConfig(
        provider="openai",
        thinking_level="high",
        api_key="test",
        base_url="https://api.openai.com/v1",
        default_headers={},
    )

    msg = AssistantMessage(content=[TextContent(text="Looks good.\n<approved/>")])
    event = TurnEndEvent(turn=1, assistant_message=msg, tool_results=[])

    async def fake_run(*args, **kwargs):
        yield event

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("vtx.ai.agent.loop.Agent.run", fake_run)
        res = await run_completion_audit(
            record,
            cwd=str(goal_cwd),
            provider=mock_provider,
            model="gpt-4o",
            model_provider="openai",
        )
        assert res.approved is True
        assert "<approved/>" in res.summary

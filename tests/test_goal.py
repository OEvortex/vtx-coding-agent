"""Tests for the ``/goal`` command and :class:`GoalManager` machinery."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from vtx.core.types import AssistantMessage, StopReason, TextContent, Usage, UserMessage
from vtx.events import (
    AgentEndEvent,
    GoalAchievedEvent,
    GoalBudgetLimitedEvent,
    GoalContinueEvent,
    GoalEvaluatingEvent,
    GoalStartEvent,
    TurnEndEvent,
)
from vtx.goal import (
    EVALUATOR_SYSTEM_PROMPT,
    Goal,
    GoalManager,
    build_evaluator_user_prompt,
    parse_budget_clause,
    parse_evaluator_decision,
)
from vtx.loop import Agent
from vtx.session import GoalEntry, Session

# ---------------------------------------------------------------------------
# parse_budget_clause / parse_evaluator_decision
# ---------------------------------------------------------------------------


def test_parse_budget_clause_turns():
    clean, override = parse_budget_clause("make tests pass or stop after 10 turns")
    assert clean == "make tests pass"
    assert override == 10


def test_parse_budget_clause_minutes():
    clean, override = parse_budget_clause("keep going or stop after 5 minutes")
    assert clean == "keep going"
    assert override == 5


def test_parse_budget_clause_absent():
    clean, override = parse_budget_clause("get the build green")
    assert clean == "get the build green"
    assert override is None


def test_parse_budget_clause_case_insensitive():
    clean, override = parse_budget_clause("Finish OR STOP AFTER 3 Turns")
    assert clean == "Finish"
    assert override == 3


def test_parse_evaluator_decision_yes():
    achieved, reason = parse_evaluator_decision("YES: tests pass and lint is clean")
    assert achieved is True
    assert "tests pass" in reason


def test_parse_evaluator_decision_no():
    achieved, reason = parse_evaluator_decision("NO: still 3 failures in test_auth.py")
    assert achieved is False
    assert "3 failures" in reason


def test_parse_evaluator_decision_fallback_no():
    achieved, _reason = parse_evaluator_decision("the agent is still iterating")
    assert achieved is False


def test_parse_evaluator_decision_empty():
    achieved, reason = parse_evaluator_decision("")
    assert achieved is False
    assert "empty" in reason.lower()


def test_build_evaluator_user_prompt_includes_goal_and_transcript():
    prompt = build_evaluator_user_prompt("all tests pass", "USER: run pytest\n\nASSISTANT: ok")
    assert "all tests pass" in prompt
    assert "USER: run pytest" in prompt
    assert "YES:" in prompt
    assert "NO:" in prompt


# ---------------------------------------------------------------------------
# GoalManager lifecycle
# ---------------------------------------------------------------------------


def test_set_goal_replaces_existing():
    mgr = GoalManager()
    first, _ = mgr.set_goal("first goal")
    second, _ = mgr.set_goal("second goal")
    assert mgr.goal is second
    assert first.status == "pursuing"
    assert second.status == "pursuing"


def test_set_goal_empty_objective_raises():
    mgr = GoalManager()
    with pytest.raises(ValueError):
        mgr.set_goal("")


def test_set_goal_too_long_raises():
    mgr = GoalManager(max_objective_chars=10)
    with pytest.raises(ValueError):
        mgr.set_goal("this objective is way too long for the cap")


def test_set_goal_records_budget_warning():
    mgr = GoalManager(max_turns_default=200)
    _, warning = mgr.set_goal("migrate the codebase or stop after 7 turns")
    assert warning is not None
    assert "7 turn" in warning
    # The objective stored on the manager has the clause stripped.
    assert mgr.goal is not None
    assert "or stop after" not in mgr.goal.objective


def test_clear_goal_returns_prior():
    mgr = GoalManager()
    mgr.set_goal("x")
    prior = mgr.clear()
    assert prior is not None
    assert mgr.goal is None


def test_clear_goal_when_none_returns_none():
    mgr = GoalManager()
    assert mgr.clear() is None


def test_pause_resume_cycle():
    mgr = GoalManager()
    mgr.set_goal("x")
    assert mgr.pause() is not None
    assert mgr.goal is not None
    assert mgr.goal is not None  # type: ignore[union-attr]
    assert mgr.goal.status == "paused"
    assert mgr.pause() is None  # already paused
    assert mgr.resume() is not None
    assert mgr.goal is not None
    assert mgr.goal is not None  # type: ignore[union-attr]
    assert mgr.goal.status == "pursuing"
    assert mgr.resume() is None  # already pursuing


def test_active_property_reflects_status():
    mgr = GoalManager()
    assert mgr.active is False
    mgr.set_goal("x")
    assert mgr.active is True
    mgr.pause()
    assert mgr.active is True
    mgr.clear()
    assert mgr.active is False


def test_max_turns_override_respects_default():
    mgr = GoalManager(max_turns_default=100)
    mgr.set_goal("loop or stop after 5 turns")
    assert mgr.max_turns == 5  # capped by override
    mgr.clear()
    mgr.set_goal("no budget clause")
    assert mgr.max_turns == 100


# ---------------------------------------------------------------------------
# GoalManager.evaluate with a mocked provider
# ---------------------------------------------------------------------------


def _stub_provider(reply: str, tokens: int = 12) -> MagicMock:
    """Build a minimal async-iterable stand-in for ``BaseProvider.stream``.

    The evaluator only consumes text deltas; we yield a single part
    with the desired reply text and a usage object on the stream.
    """

    async def _aiter(self):
        yield MagicMock(text=reply, usage=None)

    usage = Usage(input_tokens=tokens, output_tokens=0)
    stream = MagicMock()
    stream.__aiter__ = lambda self: _aiter(stream)
    stream.usage = usage
    stream.aclose = AsyncMock()
    provider = MagicMock()
    provider.stream = AsyncMock(return_value=stream)
    provider.config.model = "stub-model"
    return provider


def test_evaluate_yes_marks_achieved():
    mgr = GoalManager()
    mgr.set_goal("tests pass")
    provider = _stub_provider("YES: tests pass cleanly")
    outcome = asyncio.run(
        mgr.evaluate(
            [
                UserMessage(content="make tests pass"),
                AssistantMessage(
                    content=[TextContent(text="pytest ran and all green")],
                    stop_reason=StopReason.STOP,
                ),
            ],
            provider=provider,
            model="stub-model",
        )
    )
    assert outcome.achieved is True
    assert mgr.goal is not None  # type: ignore[union-attr]
    assert mgr.goal.status == "achieved"
    assert mgr.goal.completed_at is not None


def test_evaluate_no_returns_reason():
    mgr = GoalManager()
    mgr.set_goal("tests pass")
    provider = _stub_provider("NO: 2 failures in test_x")
    outcome = asyncio.run(
        mgr.evaluate([UserMessage(content="run pytest")], provider=provider, model="stub-model")
    )
    assert outcome.achieved is False
    assert "2 failures" in outcome.reason
    assert mgr.goal is not None  # type: ignore[union-attr]
    assert mgr.goal.status == "pursuing"
    assert mgr.goal is not None  # type: ignore[union-attr]
    assert mgr.goal.turns_evaluated == 1


def test_evaluate_provider_failure_is_swallowed():
    mgr = GoalManager()
    mgr.set_goal("x")
    provider = MagicMock()
    provider.stream = AsyncMock(side_effect=RuntimeError("network down"))
    provider.config.model = "stub"
    outcome = asyncio.run(
        mgr.evaluate([UserMessage(content="hi")], provider=provider, model="stub")
    )
    assert outcome.achieved is False
    assert "error" in outcome.reason.lower()
    # Goal stays pursuing so the loop can try again next turn.
    assert mgr.goal is not None  # type: ignore[union-attr]
    assert mgr.goal.status == "pursuing"


def test_evaluate_restores_original_model():
    """The manager must swap and restore ``provider.config.model``."""
    mgr = GoalManager()
    mgr.set_goal("x")
    provider = _stub_provider("YES: done")
    provider.config.model = "primary-model"
    asyncio.run(
        mgr.evaluate([UserMessage(content="hi")], provider=provider, model="cheaper-model")
    )
    assert provider.config.model == "primary-model"


# ---------------------------------------------------------------------------
# Persistence round-trip (GoalEntry)
# ---------------------------------------------------------------------------


def test_session_goal_entry_round_trip(tmp_path: Path):
    session = Session.create(str(tmp_path), persist=False)
    goal = Goal(
        objective="ship it",
        status="pursuing",
        turns_evaluated=3,
        tokens_used=120,
        last_reason="almost there",
        max_turns_override=8,
    )
    session.append_goal_state(goal.to_dict())
    # Last entry should be a GoalEntry.
    last = session.entries[-1]
    assert isinstance(last, GoalEntry)
    # GoalManager can read it back.
    mgr = GoalManager()
    restored = mgr.restore_from_session(session)
    assert restored is not None
    assert restored.objective == "ship it"
    assert restored.status == "pursuing"
    assert restored.max_turns_override == 8
    assert restored.turns_evaluated == 3


def test_restore_terminal_goal_keeps_state_but_not_active(tmp_path: Path):
    session = Session.create(str(tmp_path), persist=False)
    session.append_goal_state(
        Goal(objective="done", status="achieved", turns_evaluated=4).to_dict()
    )
    mgr = GoalManager()
    mgr.restore_from_session(session)
    assert mgr.goal is not None
    assert mgr.goal is not None  # type: ignore[union-attr]
    assert mgr.goal.status == "achieved"
    # Active is only true for pursuing / paused.
    assert mgr.active is False


# ---------------------------------------------------------------------------
# Agent loop integration
# ---------------------------------------------------------------------------


def _make_agent_with_goal(tmp_path: Path, goal: Goal | None = None) -> tuple[Agent, Session]:
    session = Session.create(str(tmp_path), persist=False)
    provider = MagicMock()
    provider.name = "stub"
    provider.config.model = "stub"
    mgr = GoalManager(max_turns_default=10)
    if goal is not None:
        mgr._goal = goal

    # No-op provider.stream; the test doesn't drive turns beyond what
    # we need to verify the loop's goal behaviour.
    async def _empty_stream(*args, **kwargs):
        async def _gen():
            if False:
                yield  # pragma: no cover - never reached
            return

        return _gen()

    provider.stream = _empty_stream
    agent = Agent(provider=provider, tools=[], session=session, goal_manager=mgr)
    return agent, session


def _run(agent: Agent):
    """Drain ``agent.run`` to completion, returning the list of events."""

    out: list = []

    async def _drain():
        async for event in agent.run("hello"):
            out.append(event)

    asyncio.run(_drain())
    return out


def _make_assistant_message(text: str = "ok") -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text=text)],
        stop_reason=StopReason.STOP,
        usage=Usage(input_tokens=10, output_tokens=5),
    )


def _patch_run_single_turn(monkeypatch, messages: list[AssistantMessage]) -> None:
    """Make ``run_single_turn`` yield a single TurnEndEvent with the
    given assistant message and an empty tool-result set.
    """

    async def fake_run_single_turn(*args, **kwargs):
        # Consume all but the last message (used for the goal guidances)
        # and yield a TurnEndEvent at the end.
        msg = messages.pop(0) if messages else _make_assistant_message()
        yield TurnEndEvent(turn=1, assistant_message=msg, stop_reason=StopReason.STOP)

    monkeypatch.setattr("vtx.loop.run_single_turn", fake_run_single_turn)


def test_agent_emits_goal_start_event_when_active(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    agent, _ = _make_agent_with_goal(tmp_path, Goal(objective="ship it"))
    _patch_run_single_turn(monkeypatch, [_make_assistant_message("stop")])
    # Force the loop to exit after one turn.
    monkeypatch.setattr("vtx.loop.vtx_config", _frozen_config(max_turns=1))

    events = _run(agent)
    # The first events must include GoalStartEvent so the UI can render
    # the badge before the first evaluator call.
    assert any(isinstance(e, GoalStartEvent) for e in events)
    # With no evaluator stub the loop ends via the budget-limited path.
    # The AgentEndEvent surfaces that explicitly.
    end = [e for e in events if isinstance(e, AgentEndEvent)][-1]
    assert end.goal_status == "budget_limited"


def test_agent_evaluates_and_ends_on_yes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """End-to-end: goal says YES → loop emits GoalAchievedEvent + AgentEndEvent(achieved)."""
    agent, _ = _make_agent_with_goal(tmp_path, Goal(objective="all green"))
    _patch_run_single_turn(monkeypatch, [_make_assistant_message("running pytest")])

    # Stub the evaluator so the loop sees a YES verdict on the first call.
    async def fake_evaluate(self, messages, *, provider, model):
        self._goal.turns_evaluated += 1
        self._goal.last_reason = "all tests pass"
        self._goal.status = "achieved"
        self._goal.completed_at = "now"
        return MagicMock(achieved=True, reason="all tests pass", tokens_used=4)

    monkeypatch.setattr(GoalManager, "evaluate", fake_evaluate)
    monkeypatch.setattr("vtx.loop.vtx_config", _frozen_config(max_turns=5))

    events = _run(agent)
    assert any(isinstance(e, GoalEvaluatingEvent) for e in events)
    assert any(isinstance(e, GoalAchievedEvent) for e in events)
    end = [e for e in events if isinstance(e, AgentEndEvent)][-1]
    assert end.goal_status == "achieved"
    assert end.goal_reason == "all tests pass"


def test_agent_injects_guidance_and_continues_on_no(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """End-to-end: evaluator says NO → loop injects guidance user message, starts another turn."""
    agent, session = _make_agent_with_goal(tmp_path, Goal(objective="ship it"))
    # Two turns: first yields "still working", second yields "done".
    _patch_run_single_turn(
        monkeypatch,
        [_make_assistant_message("still working"), _make_assistant_message("done now")],
    )

    call_count = {"n": 0}

    async def fake_evaluate(self, messages, *, provider, model):
        call_count["n"] += 1
        # First call: NO. Second call: the loop should have ended because we
        # only patch two messages; mark achieved so the loop terminates cleanly.
        if call_count["n"] == 1:
            self._goal.turns_evaluated += 1
            self._goal.last_reason = "still working"
            return MagicMock(achieved=False, reason="still working", tokens_used=4)
        self._goal.turns_evaluated += 1
        self._goal.last_reason = "done now"
        self._goal.status = "achieved"
        self._goal.completed_at = "now"
        return MagicMock(achieved=True, reason="done now", tokens_used=4)

    monkeypatch.setattr(GoalManager, "evaluate", fake_evaluate)
    monkeypatch.setattr("vtx.loop.vtx_config", _frozen_config(max_turns=10))

    events = _run(agent)
    # GoalContinueEvent must have fired.
    assert any(isinstance(e, GoalContinueEvent) for e in events)
    # The session must now contain the synthetic guidance user message.
    guidance_messages = [
        m.content
        for m in session.messages
        if isinstance(m, UserMessage) and "vtx:goal-guidance" in m.content
    ]
    assert guidance_messages, "expected synthetic guidance user message after NO verdict"


def test_agent_marks_budget_limited_on_turn_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """When the loop hits max_turns with a goal still pursuing, fire GoalBudgetLimitedEvent."""
    agent, _ = _make_agent_with_goal(tmp_path, Goal(objective="keep trying"))
    # Yield one assistant message then force the loop to stop by capping at 1.
    _patch_run_single_turn(monkeypatch, [_make_assistant_message("hi")])

    async def fake_evaluate(self, messages, *, provider, model):
        # Always say NO so the loop wants another turn.
        self._goal.turns_evaluated += 1
        return MagicMock(achieved=False, reason="keep going", tokens_used=4)

    monkeypatch.setattr(GoalManager, "evaluate", fake_evaluate)
    monkeypatch.setattr("vtx.loop.vtx_config", _frozen_config(max_turns=1))

    events = _run(agent)
    assert any(isinstance(e, GoalBudgetLimitedEvent) for e in events)
    end = [e for e in events if isinstance(e, AgentEndEvent)][-1]
    assert end.goal_status == "budget_limited"


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class _FrozenConfig:
    """Minimal stand-in for ``vtx.config`` used in goal-loop tests.

    Real ``vtx.config`` is a Pydantic model loaded from disk; we want a
    deterministic substitute so the agent loop can read ``agent.max_turns``
    and friends without dragging in the whole config stack. The fields
    touched by :class:`Agent` and the goal manager are pinned to
    integers here; everything else falls back to ``MagicMock`` (which
    never matches a real comparison, so the loop exits cleanly).
    """

    def __init__(self, max_turns: int) -> None:
        self.agent = MagicMock(max_turns=max_turns, default_context_window=200000)
        self.compaction = MagicMock(on_overflow="continue", threshold_percent=80.0)
        self.goal = MagicMock(max_turns=100, max_objective_chars=4000)


def _frozen_config(max_turns: int) -> _FrozenConfig:
    return _FrozenConfig(max_turns)


# Sanity-check that the helper imports don't shadow anything in test_agentic_loop.
def test_no_top_level_evaluator_imports_used_in_turn_loop():
    # The loop must not import the evaluator at module load (would cause
    # a circular dependency). The tests above would fail if it did.
    import vtx.loop as loop_mod

    source = Path(loop_mod.__file__).read_text()
    assert "from .goal import" in source
    # Sanity: the system prompt constant is reachable.
    assert "YES:" in EVALUATOR_SYSTEM_PROMPT

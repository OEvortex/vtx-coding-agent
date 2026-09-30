"""Tests for the two refine extension hooks.

Refinement writes to durable state that outlives the session, so an extension
needs a way to watch it happen (``refine_complete``) and a way to stop it
(``session_before_refine``). Both are pinned here at the loop level, where they
are actually fired, because a hook that is registered but never emitted is
indistinguishable from one that does not exist.
"""

import pytest

from vtx.ai.agent.config import HarnessConfig, set_harness_config
from vtx.ai.agent.extensions import ALL_EVENTS, REFINE_COMPLETE, SESSION_BEFORE_REFINE, EventBus
from vtx.ai.agent.loop import Agent
from vtx.ai.agent.rlm import refine as refine_mod
from vtx.ai.agent.rlm.refine import (
    AUTO_REFINE_REASON_TURN_INTERVAL,
    AutoRefineReview,
    RefinementOutcome,
)
from vtx.ai.agent.rlm.registry import reset_state
from vtx.ai.agent.session import Session
from vtx.ai.providers.mock import MockProvider


@pytest.fixture(autouse=True)
def clean_state():
    reset_state()
    yield
    reset_state()


def _agent(tmp_path, bus: EventBus | None = None) -> Agent:
    agent = Agent(
        MockProvider(), [], Session.in_memory(cwd=str(tmp_path)), cwd=str(tmp_path), extensions=bus
    )
    agent._auto_refine_turns_since_review = 1
    return agent


def _approve() -> AutoRefineReview:
    return AutoRefineReview(
        should_refine=True, rationale="repeated failure", instructions="use pytest"
    )


def _patch_review(monkeypatch, review: AutoRefineReview) -> None:
    async def _review(**kwargs):
        return review

    monkeypatch.setattr(refine_mod, "review_auto_refine", _review)


def _patch_run(monkeypatch, calls: list[dict], outcome: RefinementOutcome | None = None) -> None:
    async def _run(**kwargs):
        calls.append(kwargs)
        return outcome or RefinementOutcome(
            id="refine_auto",
            summary="kept the preference",
            applied=1,
            total=1,
            scope="local",
            notice="[auto-refinement]\n\n- create memory [local:pref] Pref: use pytest",
        )

    monkeypatch.setattr(refine_mod, "run_refinement", _run)


def _enable(monkeypatch) -> None:
    set_harness_config(HarnessConfig(auto_refine_turn_interval=1))


# =================================================================================================
# registration
# =================================================================================================


def test_both_hooks_are_subscribable_events():
    assert SESSION_BEFORE_REFINE in ALL_EVENTS
    assert REFINE_COMPLETE in ALL_EVENTS


# =================================================================================================
# refine_complete
# =================================================================================================


@pytest.mark.asyncio
async def test_complete_hook_receives_the_outcome(tmp_path, monkeypatch):
    _enable(monkeypatch)
    _patch_review(monkeypatch, _approve())
    calls: list[dict] = []
    _patch_run(monkeypatch, calls)
    bus = EventBus()
    seen: list[dict] = []

    @bus.on(REFINE_COMPLETE)
    def _record(event, payload):
        seen.append(payload)

    await _agent(tmp_path, bus)._maybe_auto_refine(AUTO_REFINE_REASON_TURN_INTERVAL, None)

    assert len(seen) == 1
    assert seen[0]["applied"] == 1
    assert seen[0]["total"] == 1
    assert isinstance(seen[0]["outcome"], RefinementOutcome)


@pytest.mark.asyncio
async def test_complete_hook_fires_even_when_the_pass_fails(tmp_path, monkeypatch):
    """A crash mid-pass is exactly when an extension wants to know, so the hook
    is not silently skipped on the error path."""
    _enable(monkeypatch)
    _patch_review(monkeypatch, _approve())

    async def _boom(**kwargs):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(refine_mod, "run_refinement", _boom)
    bus = EventBus()
    seen: list[dict] = []

    @bus.on(REFINE_COMPLETE)
    def _record(event, payload):
        seen.append(payload)

    await _agent(tmp_path, bus)._maybe_auto_refine(AUTO_REFINE_REASON_TURN_INTERVAL, None)

    assert len(seen) == 1
    assert seen[0]["outcome"] is None
    assert seen[0]["applied"] == 0


@pytest.mark.asyncio
async def test_complete_hook_does_not_fire_when_the_gate_declines(tmp_path, monkeypatch):
    """No pass, no completion: firing it would make a handler count passes it
    never saw."""
    _enable(monkeypatch)
    _patch_review(monkeypatch, AutoRefineReview(should_refine=False, rationale="noise"))
    calls: list[dict] = []
    _patch_run(monkeypatch, calls)
    bus = EventBus()
    seen: list[dict] = []

    @bus.on(REFINE_COMPLETE)
    def _record(event, payload):
        seen.append(payload)

    await _agent(tmp_path, bus)._maybe_auto_refine(AUTO_REFINE_REASON_TURN_INTERVAL, None)

    assert not seen
    assert not calls


# =================================================================================================
# session_before_refine
# =================================================================================================


@pytest.mark.asyncio
async def test_before_hook_sees_the_reason_and_the_verdict(tmp_path, monkeypatch):
    _enable(monkeypatch)
    _patch_review(monkeypatch, _approve())
    calls: list[dict] = []
    _patch_run(monkeypatch, calls)
    bus = EventBus()
    seen: list[dict] = []

    @bus.on(SESSION_BEFORE_REFINE)
    def _record(event, payload):
        seen.append(payload)

    await _agent(tmp_path, bus)._maybe_auto_refine(AUTO_REFINE_REASON_TURN_INTERVAL, None)

    assert len(seen) == 1
    assert seen[0]["reason"] == AUTO_REFINE_REASON_TURN_INTERVAL
    assert isinstance(seen[0]["review"], AutoRefineReview)
    assert calls, "a non-blocking hook must not stop the pass"


@pytest.mark.asyncio
async def test_a_blocking_handler_stops_the_pass(tmp_path, monkeypatch):
    _enable(monkeypatch)
    _patch_review(monkeypatch, _approve())
    calls: list[dict] = []
    _patch_run(monkeypatch, calls)
    bus = EventBus()

    @bus.on(SESSION_BEFORE_REFINE)
    def _veto(event, payload):
        return {"block": True, "reason": "refinement is disabled by policy"}

    events = await _agent(tmp_path, bus)._maybe_auto_refine(AUTO_REFINE_REASON_TURN_INTERVAL, None)

    assert not calls, "a vetoed pass must not run"
    assert any("refinement is disabled by policy" in e.text for e in events)


@pytest.mark.asyncio
async def test_a_veto_is_not_retried_into_the_pass_on_the_next_boundary(tmp_path, monkeypatch):
    """A veto must count as a completed review, not leave the approved verdict
    pending so the next boundary runs the pass the extension just refused."""
    _enable(monkeypatch)
    _patch_review(monkeypatch, _approve())
    calls: list[dict] = []
    _patch_run(monkeypatch, calls)
    bus = EventBus()

    @bus.on(SESSION_BEFORE_REFINE)
    def _veto(event, payload):
        return {"block": True, "reason": "no"}

    agent = _agent(tmp_path, bus)
    await agent._maybe_auto_refine(AUTO_REFINE_REASON_TURN_INTERVAL, None)

    assert agent._pending_auto_refine_review is None
    assert not calls


@pytest.mark.asyncio
async def test_a_raising_handler_does_not_stop_the_pass(tmp_path, monkeypatch):
    """Extension bugs must not be able to break refinement."""
    _enable(monkeypatch)
    _patch_review(monkeypatch, _approve())
    calls: list[dict] = []
    _patch_run(monkeypatch, calls)
    bus = EventBus()

    @bus.on(SESSION_BEFORE_REFINE)
    def _explode(event, payload):
        raise ValueError("extension bug")

    await _agent(tmp_path, bus)._maybe_auto_refine(AUTO_REFINE_REASON_TURN_INTERVAL, None)

    assert calls


@pytest.mark.asyncio
async def test_no_bus_means_no_hooks_and_still_refines(tmp_path, monkeypatch):
    _enable(monkeypatch)
    _patch_review(monkeypatch, _approve())
    calls: list[dict] = []
    _patch_run(monkeypatch, calls)

    await _agent(tmp_path, None)._maybe_auto_refine(AUTO_REFINE_REASON_TURN_INTERVAL, None)

    assert calls

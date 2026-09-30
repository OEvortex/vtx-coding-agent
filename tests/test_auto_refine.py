"""Tests for auto-refinement: the review gate, the loop's throttling state
machine, the mode-aware plan contract, and the tool-first ``refine`` tool."""

import asyncio
import json

import pytest

from vtx.ai.agent.config import HarnessConfig, get_harness_config, set_harness_config
from vtx.ai.agent.loop import Agent
from vtx.ai.agent.rlm import refine as refine_mod
from vtx.ai.agent.rlm.harness import get_harness_state
from vtx.ai.agent.rlm.refine import (
    AUTO_REFINE_REASON_COMPACT,
    AUTO_REFINE_REASON_TURN_INTERVAL,
    MODE_CODE_FIRST,
    MODE_TOOL_FIRST,
    AutoRefineReview,
    RefinementOutcome,
    auto_refine_instructions,
    harness_digest_for_prompt,
    harness_digest_with_fingerprint,
    local_state_dir,
    parse_auto_refine_review,
    plan_refinement,
    review_auto_refine,
)
from vtx.ai.agent.rlm.registry import bridge_session_id, get_registry, reset_state
from vtx.ai.agent.session import Session
from vtx.ai.agent.tools import get_default_tools, get_parent_only_tools, get_tool
from vtx.ai.agent.tools.refine import RefineParams, RefineTool
from vtx.ai.base import LLMStream
from vtx.ai.providers.mock import MockProvider
from vtx.core.types import TextPart, UserMessage


@pytest.fixture(autouse=True)
def clean_state():
    original = get_harness_config()
    reset_state()
    yield
    reset_state()
    set_harness_config(original)


class _JsonProvider:
    """Minimal provider returning one JSON payload as a text stream."""

    def __init__(self, payload):
        self.payload = payload
        self.system_prompt = None
        self.kwargs = None

    async def stream(self, messages, *, system_prompt=None, tools=None, **kwargs):
        self.system_prompt = system_prompt
        self.messages = messages
        self.kwargs = kwargs
        stream = LLMStream()

        async def gen():
            yield TextPart(text=json.dumps(self.payload))

        stream.set_iterator(gen())
        return stream


def _agent(tmp_path, **kwargs) -> Agent:
    return Agent(MockProvider(), [], Session.in_memory(), cwd=str(tmp_path), **kwargs)


def _approve(rationale="repeated failure", instructions=None) -> AutoRefineReview:
    return AutoRefineReview(should_refine=True, rationale=rationale, instructions=instructions)


# =================================================================================================
# review gate: prompt, parsing, instruction text
# =================================================================================================


def test_parse_auto_refine_review_accepts_approve():
    review = parse_auto_refine_review(
        'noise {"shouldRefine": true, "rationale": "same test failed 3x",'
        ' "instructions": "remember to use pytest"}'
    )
    assert review.should_refine is True
    assert review.rationale == "same test failed 3x"
    assert review.instructions == "remember to use pytest"


def test_parse_auto_refine_review_defaults_missing_fields():
    review = parse_auto_refine_review('{"rationale": "nothing durable"}')
    assert review.should_refine is False
    assert review.rationale == "nothing durable"
    assert review.instructions is None


def test_parse_auto_refine_review_rejects_non_object():
    with pytest.raises(ValueError, match="must be an object"):
        parse_auto_refine_review("```json\n[1, 2, 3]\n```")


def test_auto_refine_instructions_carry_reason_and_rationale():
    text = auto_refine_instructions(AUTO_REFINE_REASON_COMPACT, _approve("flaky tmp path", "x=1"))
    assert "triggered by compact" in text
    assert "flaky tmp path" in text
    assert "Reviewer instructions: x=1" in text
    assert "empty edits array" in text


@pytest.mark.asyncio
async def test_review_auto_refine_sends_gate_prompt(tmp_path):
    provider = _JsonProvider({"shouldRefine": False, "rationale": "one-off noise"})
    cwd = str(tmp_path)
    state = get_harness_state(local_state_dir(bridge_session_id(), cwd))
    state.create("memory", "Pref", "Use pytest.", id="pref")

    review = await review_auto_refine(
        messages=[UserMessage(content="run the tests")],
        provider=provider,
        states=(state,),
        history=[],
        reason=AUTO_REFINE_REASON_TURN_INTERVAL,
        turns_since_last_review=25,
    )

    assert review.should_refine is False
    assert provider.system_prompt == refine_mod.AUTO_REFINE_REVIEW_SYSTEM_PROMPT
    # The gate is a small verdict call, not a full plan budget.
    assert provider.kwargs["max_tokens"] == refine_mod.AUTO_REFINE_REVIEW_MAX_OUTPUT_TOKENS
    sent = provider.messages[0].content
    assert "turn_interval; 25 assistant turns since last auto-refine review" in sent
    assert "Use pytest." in sent


@pytest.mark.asyncio
async def test_review_auto_refine_honors_cancel(tmp_path):
    cancel = asyncio.Event()
    cancel.set()
    with pytest.raises(RuntimeError, match="cancelled"):
        await review_auto_refine(
            messages=[],
            provider=_JsonProvider({"shouldRefine": True}),
            states=(get_harness_state(local_state_dir(bridge_session_id(), str(tmp_path))),),
            history=[],
            reason=AUTO_REFINE_REASON_TURN_INTERVAL,
            turns_since_last_review=25,
            cancel_event=cancel,
        )


# =================================================================================================
# mode-aware plan contract
# =================================================================================================


class _CaptureProvider:
    def __init__(self):
        self.prompt = None

    async def stream(self, messages, *, system_prompt=None, tools=None, **kwargs):
        self.prompt = messages[0].content
        stream = LLMStream()

        async def gen():
            yield TextPart(text='{"summary": "s", "edits": []}')

        stream.set_iterator(gen())
        return stream


async def _plan_prompt(tmp_path, mode):
    provider = _CaptureProvider()
    state = get_harness_state(local_state_dir(bridge_session_id(), str(tmp_path)))
    await plan_refinement(messages=[], provider=provider, states=(state,), history=[], mode=mode)
    return provider.prompt


@pytest.mark.asyncio
async def test_plan_refinement_tool_first_gets_mode_contract(tmp_path):
    prompt = await _plan_prompt(tmp_path, MODE_TOOL_FIRST)
    assert "<mode_contract>" in prompt
    # The REPL-native call form cannot execute in tool-first mode.
    assert "no persistent Python kernel" in prompt
    assert '"type": "tool_first"' in prompt
    assert "`task`-tool delegation spec" in prompt


@pytest.mark.asyncio
async def test_plan_refinement_code_first_has_no_mode_contract(tmp_path):
    prompt = await _plan_prompt(tmp_path, MODE_CODE_FIRST)
    assert "<mode_contract>" not in prompt


@pytest.mark.asyncio
async def test_plan_refinement_keeps_user_instructions(tmp_path):
    provider = _CaptureProvider()
    state = get_harness_state(local_state_dir(bridge_session_id(), str(tmp_path)))
    await plan_refinement(
        messages=[],
        provider=provider,
        states=(state,),
        history=[],
        instructions="always check git status",
    )
    assert "always check git status" in provider.prompt


# =================================================================================================
# loop throttling state machine
# =================================================================================================


@pytest.mark.asyncio
async def test_run_consults_auto_refine_gate_at_turn_boundary(monkeypatch, tmp_path):
    set_harness_config(HarnessConfig(auto_refine_turn_interval=1))
    agent = Agent(MockProvider(scenario="simple_text"), [], Session.in_memory(), cwd=str(tmp_path))
    calls = []
    monkeypatch.setattr(refine_mod, "review_auto_refine", _record_review(calls, _decline()))
    monkeypatch.setattr(refine_mod, "run_refinement", _boom("a declined review must not refine"))

    [_ async for _ in agent.run("do the thing")]

    assert len(calls) == 1
    assert calls[0]["reason"] == AUTO_REFINE_REASON_TURN_INTERVAL
    assert calls[0]["turns_since_last_review"] == 1
    # A declined review applies nothing, so the run still stops on its own.
    assert agent.session.all_messages[-1].role == "assistant"


@pytest.mark.asyncio
async def test_run_keeps_going_after_auto_refinement_applies_edits(monkeypatch, tmp_path):
    set_harness_config(HarnessConfig(auto_refine_turn_interval=1))
    agent = Agent(MockProvider(scenario="simple_text"), [], Session.in_memory(), cwd=str(tmp_path))
    monkeypatch.setattr(refine_mod, "review_auto_refine", _record_review([], _approve()))

    async def _run(**kwargs):
        return RefinementOutcome(
            id="refine_auto",
            summary="kept the preference",
            applied=1,
            total=1,
            scope="local",
            notice="[auto-refinement]\n\n- create memory [local:pref] Pref: use pytest",
        )

    monkeypatch.setattr(refine_mod, "run_refinement", _run)

    events = [_ async for _ in agent.run("do the thing")]

    # The model sees the notice and resumes once on the rebuilt prompt.
    assert any(getattr(e, "kind", "") == "refinement" for e in events)
    assert any(getattr(e, "type", "") == "agent_end" for e in events)
    assert any(
        isinstance(m, UserMessage) and m.content.startswith("[auto-refinement]")
        for m in agent.session.all_messages
    )


def test_auto_refine_skipped_when_disabled(monkeypatch, tmp_path):
    set_harness_config(HarnessConfig(auto_refine_enabled=False, auto_refine_turn_interval=1))
    agent = _agent(tmp_path)
    monkeypatch.setattr(
        refine_mod, "review_auto_refine", _boom("review must not run when disabled")
    )
    agent._auto_refine_turns_since_review = 99

    assert asyncio.run(agent._maybe_auto_refine(AUTO_REFINE_REASON_TURN_INTERVAL, None)) == []


def test_auto_refine_skipped_for_subagent_depth(monkeypatch, tmp_path):
    set_harness_config(HarnessConfig(auto_refine_turn_interval=1))
    agent = _agent(tmp_path, depth=1)
    monkeypatch.setattr(refine_mod, "review_auto_refine", _boom("child must not review"))
    agent._auto_refine_turns_since_review = 99

    assert asyncio.run(agent._maybe_auto_refine(AUTO_REFINE_REASON_TURN_INTERVAL, None)) == []


def test_auto_refine_waits_for_turn_interval(monkeypatch, tmp_path):
    set_harness_config(HarnessConfig(auto_refine_turn_interval=25))
    agent = _agent(tmp_path)
    calls = []
    monkeypatch.setattr(refine_mod, "review_auto_refine", _record_review(calls, _decline()))
    agent._auto_refine_turns_since_review = 24

    assert asyncio.run(agent._maybe_auto_refine(AUTO_REFINE_REASON_TURN_INTERVAL, None)) == []
    assert calls == []

    agent._auto_refine_turns_since_review = 25
    asyncio.run(agent._maybe_auto_refine(AUTO_REFINE_REASON_TURN_INTERVAL, None))
    assert len(calls) == 1


def test_auto_refine_compact_trigger_ignores_turn_interval(monkeypatch, tmp_path):
    set_harness_config(HarnessConfig(auto_refine_turn_interval=25, auto_refine_on_compact=True))
    agent = _agent(tmp_path)
    calls = []
    monkeypatch.setattr(refine_mod, "review_auto_refine", _record_review(calls, _decline()))
    agent._auto_refine_turns_since_review = 1

    asyncio.run(agent._maybe_auto_refine(AUTO_REFINE_REASON_COMPACT, None))
    assert len(calls) == 1
    assert calls[0]["reason"] == AUTO_REFINE_REASON_COMPACT


def test_auto_refine_compact_trigger_falls_back_to_interval(monkeypatch, tmp_path):
    set_harness_config(HarnessConfig(auto_refine_turn_interval=25, auto_refine_on_compact=False))
    agent = _agent(tmp_path)
    calls = []
    monkeypatch.setattr(refine_mod, "review_auto_refine", _record_review(calls, _decline()))
    agent._auto_refine_turns_since_review = 3

    assert asyncio.run(agent._maybe_auto_refine(AUTO_REFINE_REASON_COMPACT, None)) == []
    assert calls == []


def test_auto_refine_approved_review_runs_pass(monkeypatch, tmp_path):
    set_harness_config(HarnessConfig(auto_refine_turn_interval=1))
    agent = _agent(tmp_path)
    monkeypatch.setattr(
        refine_mod, "review_auto_refine", _record_review([], _approve("3 identical failures"))
    )
    runs = []

    async def _run(**kwargs):
        runs.append(kwargs)
        return RefinementOutcome(
            id="refine_auto",
            summary="persisted the pytest preference",
            applied=1,
            total=1,
            scope="local",
            notice="[auto-refinement]\n\n- create memory [local:pref] Pref: use pytest",
        )

    monkeypatch.setattr(refine_mod, "run_refinement", _run)
    reloads = []
    monkeypatch.setattr(agent, "reload_context", lambda: reloads.append(True))
    agent._auto_refine_turns_since_review = 1

    events = asyncio.run(agent._maybe_auto_refine(AUTO_REFINE_REASON_TURN_INTERVAL, None))

    assert len(runs) == 1
    assert runs[0]["source"] == "auto"
    assert "3 identical failures" in runs[0]["instructions"]
    assert "triggered by turn_interval" in runs[0]["instructions"]
    # Auto-refine never promotes to the cross-session store on its own.
    assert runs[0].get("global_", False) is False
    assert [e.kind for e in events] == ["refinement"]
    assert events[0].text.startswith("Auto-refine refine_auto:")
    assert agent.session.all_messages[-1].content.startswith("[auto-refinement]")
    assert reloads == [True]
    assert agent._auto_refine_in_progress is False
    assert agent._auto_refine_turns_since_review == 0
    assert get_registry(bridge_session_id()).refine_in_flight is False


def test_auto_refine_zero_edits_reports_notice_only(monkeypatch, tmp_path):
    set_harness_config(HarnessConfig(auto_refine_turn_interval=1))
    agent = _agent(tmp_path)
    monkeypatch.setattr(refine_mod, "review_auto_refine", _record_review([], _approve()))
    reloads = []
    monkeypatch.setattr(agent, "reload_context", lambda: reloads.append(True))

    async def _run(**kwargs):
        return RefinementOutcome(
            id="refine_none",
            summary="nothing worth keeping",
            applied=0,
            total=1,
            scope="local",
            notice=None,
        )

    monkeypatch.setattr(refine_mod, "run_refinement", _run)
    agent._auto_refine_turns_since_review = 1

    events = asyncio.run(agent._maybe_auto_refine(AUTO_REFINE_REASON_TURN_INTERVAL, None))

    assert [e.kind for e in events] == ["notice"]
    assert "no edits applied" in events[0].text
    # Nothing changed, so the prompt is not rebuilt and no notice is injected.
    assert reloads == []
    assert not any(
        isinstance(m, UserMessage) and m.content.startswith("[auto-refinement]")
        for m in agent.session.all_messages
    )


def test_auto_refine_pass_failure_surfaces_error_event(monkeypatch, tmp_path):
    set_harness_config(HarnessConfig(auto_refine_turn_interval=1))
    agent = _agent(tmp_path)
    monkeypatch.setattr(refine_mod, "review_auto_refine", _record_review([], _approve()))

    async def _boom_run(**kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(refine_mod, "run_refinement", _boom_run)
    agent._auto_refine_turns_since_review = 1

    events = asyncio.run(agent._maybe_auto_refine(AUTO_REFINE_REASON_TURN_INTERVAL, None))

    assert [e.kind for e in events] == ["refinement_error"]
    assert "provider down" in events[0].text
    assert agent._auto_refine_in_progress is False


def test_auto_refine_decline_resets_interval_and_cools_down(monkeypatch, tmp_path):
    set_harness_config(HarnessConfig(auto_refine_turn_interval=1))
    agent = _agent(tmp_path)
    calls = []
    monkeypatch.setattr(refine_mod, "review_auto_refine", _record_review(calls, _decline()))
    monkeypatch.setattr(refine_mod, "run_refinement", _boom("a declined review must not refine"))
    agent._auto_refine_turns_since_review = 1

    assert asyncio.run(agent._maybe_auto_refine(AUTO_REFINE_REASON_TURN_INTERVAL, None)) == []
    assert len(calls) == 1
    assert agent._auto_refine_turns_since_review == 0
    assert agent._auto_refine_last_review_at > 0

    # Cooldown is 20 minutes by default, so the next boundary stays silent.
    agent._auto_refine_turns_since_review = 1
    assert asyncio.run(agent._maybe_auto_refine(AUTO_REFINE_REASON_TURN_INTERVAL, None)) == []
    assert len(calls) == 1


def test_auto_refine_review_failure_stamps_cooldown(monkeypatch, tmp_path):
    set_harness_config(HarnessConfig(auto_refine_turn_interval=1))
    agent = _agent(tmp_path)

    async def _explode(**kwargs):
        raise RuntimeError("unparseable gate output")

    monkeypatch.setattr(refine_mod, "review_auto_refine", _explode)
    agent._auto_refine_turns_since_review = 1

    # A broken gate must not raise into the loop, and must not retry next turn.
    assert asyncio.run(agent._maybe_auto_refine(AUTO_REFINE_REASON_TURN_INTERVAL, None)) == []
    assert agent._auto_refine_last_review_at > 0
    assert agent._auto_refine_in_progress is False


def test_auto_refine_approved_review_kept_when_cancelled(monkeypatch, tmp_path):
    set_harness_config(HarnessConfig(auto_refine_turn_interval=1))
    agent = _agent(tmp_path)
    review = _approve("kept for the next run")
    monkeypatch.setattr(refine_mod, "review_auto_refine", _record_review([], review))
    monkeypatch.setattr(refine_mod, "run_refinement", _boom("cancelled run must not refine"))
    agent._auto_refine_turns_since_review = 1

    cancel = asyncio.Event()
    cancel.set()
    events = asyncio.run(agent._maybe_auto_refine(AUTO_REFINE_REASON_TURN_INTERVAL, cancel))

    assert events == []
    assert agent._pending_auto_refine_review == (AUTO_REFINE_REASON_TURN_INTERVAL, review)
    assert agent._auto_refine_in_progress is False


def test_auto_refine_approved_review_replayed_next_boundary(monkeypatch, tmp_path):
    set_harness_config(HarnessConfig(auto_refine_turn_interval=1))
    agent = _agent(tmp_path)
    review = _approve("replayed")
    monkeypatch.setattr(refine_mod, "review_auto_refine", _record_review([], review))
    runs = []

    async def _run(**kwargs):
        runs.append(kwargs)
        return RefinementOutcome(
            id="refine_replay",
            summary="s",
            applied=1,
            total=1,
            scope="local",
            notice="[auto-refinement]\n\n- create memory [local:x] X: y",
        )

    monkeypatch.setattr(refine_mod, "run_refinement", _run)
    agent._pending_auto_refine_review = (AUTO_REFINE_REASON_TURN_INTERVAL, review)

    events = asyncio.run(agent._maybe_auto_refine(AUTO_REFINE_REASON_COMPACT, None))

    assert len(runs) == 1
    assert [e.kind for e in events] == ["refinement"]
    assert agent._pending_auto_refine_review is None


# =================================================================================================
# tool-first digest + refine tool
# =================================================================================================


def test_tool_first_digest_tells_model_about_refine_tool(tmp_path, monkeypatch):
    cwd = str(tmp_path)
    state = get_harness_state(local_state_dir(bridge_session_id(), cwd))
    state.create("memory", "Pref", "Use pytest.", id="pref")

    monkeypatch.setattr(refine_mod, "current_mode", lambda: MODE_TOOL_FIRST)
    digest, fingerprint = harness_digest_with_fingerprint(
        bridge_session_id(), cwd, mode=MODE_TOOL_FIRST
    )

    # Delivered as a context message, not folded into the cached system prompt:
    # relevance ranking rewrites the digest per task, which would invalidate the
    # provider's system-prompt prefix on nearly every turn.
    from vtx.ai.agent.prompts import build_system_prompt

    assert "# Continual Harness State" not in build_system_prompt(cwd, tools=[])
    assert "# Continual Harness State" in digest
    assert "Use pytest." in digest
    assert "refine` tool" in digest
    assert fingerprint


def test_harness_digest_omitted_when_store_empty(tmp_path):
    assert harness_digest_for_prompt("fresh-session", str(tmp_path), mode=MODE_TOOL_FIRST) == ""


def test_refine_tool_is_default_and_parent_only():
    assert get_tool("refine") is not None
    assert "refine" in get_default_tools()
    # The parent loop owns the drain, so a sub-agent must not queue refinements.
    assert "refine" in get_parent_only_tools()


def test_refine_tool_run_queues_turn_boundary_request():
    tool = RefineTool()
    result = asyncio.run(
        tool.execute(RefineParams(action="run", instructions="remember the flaky tmp path"))
    )
    assert result.success is True
    assert json.loads(result.result)["scheduled"] is True
    assert get_registry(bridge_session_id()).refine_pending == {
        "instructions": "remember the flaky tmp path",
        "global": False,
    }


def test_refine_tool_run_merges_with_queued_request():
    tool = RefineTool()
    asyncio.run(tool.execute(RefineParams(action="run", instructions="first", global_=True)))
    asyncio.run(tool.execute(RefineParams(action="run")))

    # Omitted fields keep the queued values instead of clearing them.
    assert get_registry(bridge_session_id()).refine_pending == {
        "instructions": "first",
        "global": True,
    }


def test_refine_tool_status_reports_queue():
    tool = RefineTool()
    idle = json.loads(asyncio.run(tool.execute(RefineParams(action="status"))).result)
    assert idle == {"status": "idle", "pending": False, "in_flight": False}

    asyncio.run(tool.execute(RefineParams(action="run")))
    queued = json.loads(asyncio.run(tool.execute(RefineParams(action="status"))).result)
    assert queued["pending"] is True


def test_refine_tool_call_format():
    tool = RefineTool()
    assert tool.format_call(RefineParams(action="status")) == "status"
    assert tool.format_call(RefineParams(instructions="a" * 100)).startswith("run · aaa")


# =================================================================================================
# helpers
# =================================================================================================


def _decline(rationale="nothing durable"):
    return AutoRefineReview(should_refine=False, rationale=rationale)


def _record_review(calls, verdict):
    async def _review(**kwargs):
        calls.append(kwargs)
        return verdict

    return _review


def _boom(message):
    async def _call(**kwargs):
        raise AssertionError(message)

    return _call

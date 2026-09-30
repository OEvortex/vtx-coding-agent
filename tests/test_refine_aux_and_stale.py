"""Tests for the two things added on top of the refinement pipeline: routing a
refinement pass to a cheaper model, and letting the auto-refine gate report
entries the trajectory has contradicted.

Both exist because of the same underlying problem — a refinement pass is the
only writer, it always runs on the session model, and it never re-examines what
it already wrote.
"""

import json
import types

import pytest

from vtx.ai.agent.loop import Agent
from vtx.ai.agent.rlm import refine as refine_mod
from vtx.ai.agent.rlm.harness import get_harness_state
from vtx.ai.agent.rlm.refine import (
    AUTO_REFINE_REASON_TURN_INTERVAL,
    STALE_REASON,
    AutoRefineReview,
    RefinementOutcome,
    known_entry_index,
    local_state_dir,
    parse_auto_refine_review,
    resolve_refine_provider,
    review_auto_refine,
    run_refinement,
    stale_instructions,
)
from vtx.ai.agent.rlm.registry import bridge_session_id, reset_state
from vtx.ai.agent.session import Session
from vtx.ai.base import LLMStream, ProviderConfig
from vtx.ai.providers.mock import MockProvider
from vtx.core.types import TextPart, UserMessage


@pytest.fixture(autouse=True)
def clean_state():
    reset_state()
    yield
    reset_state()


class _JsonProvider:
    def __init__(self, payload):
        self.payload = payload
        self.system_prompt = None
        self.calls: list[dict] = []

    async def stream(self, messages, *, system_prompt=None, tools=None, **kwargs):
        self.system_prompt = system_prompt
        self.calls.append({"messages": messages, "kwargs": kwargs})
        stream = LLMStream()

        async def gen():
            yield TextPart(text=json.dumps(self.payload))

        stream.set_iterator(gen())
        return stream


def _mock(model: str = "test-model") -> MockProvider:
    """MockProvider takes its model through ProviderConfig, like every provider."""
    return MockProvider(ProviderConfig(model=model))


def _local(cwd):
    return get_harness_state(local_state_dir(bridge_session_id(), cwd))


# =================================================================================================
# auxiliary model routing
# =================================================================================================


def test_no_configured_model_keeps_the_session_provider():
    """Unset is the default, and it must not build anything."""
    session_provider = _mock("session-model")

    provider, label = resolve_refine_provider(session_provider)

    assert provider is session_provider
    assert label == "session-model"


def test_a_blank_selector_reads_as_unset(monkeypatch):
    monkeypatch.setattr(refine_mod, "_refine_model_selector", lambda: "   ")
    session_provider = _mock("session-model")

    provider, label = resolve_refine_provider(session_provider)

    assert provider is session_provider
    assert label == "session-model"


def test_a_non_string_config_value_reads_as_unset(monkeypatch):
    """A hand-edited config can hold a number or a list here; treating that as
    unset is what keeps a bad config file from breaking every refinement."""

    class _FakeRefine:
        model = 42

    class _FakeConfig:
        refine = _FakeRefine()

    import vtx.ai.config as config_mod

    monkeypatch.setattr(config_mod, "config", _FakeConfig())
    assert refine_mod._refine_model_selector() == ""


def test_a_malformed_selector_falls_back_instead_of_raising(monkeypatch):
    monkeypatch.setattr(refine_mod, "_refine_model_selector", lambda: "no-slash-here")
    session_provider = _mock("session-model")

    provider, label = resolve_refine_provider(session_provider)

    assert provider is session_provider
    assert label == "session-model"


def test_an_unknown_model_falls_back(monkeypatch):
    monkeypatch.setattr(refine_mod, "_refine_model_selector", lambda: "openai/not-a-real-model")
    session_provider = _mock("session-model")

    provider, _label = resolve_refine_provider(session_provider)

    assert provider is session_provider


def test_a_resolvable_selector_routes_the_pass_and_labels_it(monkeypatch):
    """The whole point: the pass leaves the session model and says so."""
    aux = _mock("cheap-model")
    monkeypatch.setattr(refine_mod, "_refine_model_selector", lambda: "openai/gpt-4o-mini")
    monkeypatch.setattr(
        refine_mod, "_build_auxiliary_provider", lambda selector: (aux, "gpt-4o-mini", "openai")
    )
    session_provider = _mock("expensive-model")

    provider, label = resolve_refine_provider(session_provider)

    assert provider is aux
    assert label == "openai/gpt-4o-mini"


def test_an_unresolvable_selector_falls_back(monkeypatch):
    """A bad selector must degrade to the session model, not fail the turn."""
    monkeypatch.setattr(refine_mod, "_refine_model_selector", lambda: "openai/gpt-4o-mini")
    monkeypatch.setattr(refine_mod, "_build_auxiliary_provider", lambda selector: None)
    session_provider = _mock("session-model")

    provider, label = resolve_refine_provider(session_provider)

    assert provider is session_provider
    assert label == "session-model"


def test_the_builder_swallows_a_construction_failure(monkeypatch):
    """However the provider build fails, refinement carries on with the session
    model; a raise here would fail the whole turn instead."""
    import vtx.ai.agent.runtime as runtime_mod
    import vtx.ai.models as models_mod

    info = types.SimpleNamespace(
        api="openai",
        provider="openai",
        base_url="https://example.invalid",
        effective_id="gpt-4o-mini",
        max_tokens=4096,
        thinking_level_map=None,
    )
    monkeypatch.setattr(models_mod, "get_model", lambda model_id, provider=None: info)
    monkeypatch.setattr(refine_mod, "_auxiliary_api_key", lambda provider: "sk-test")
    monkeypatch.setattr(refine_mod, "_provider_allows_anonymous", lambda provider: False)

    def _boom(*args, **kwargs):
        raise RuntimeError("credentials missing")

    monkeypatch.setattr(runtime_mod, "create_provider", _boom)

    assert refine_mod._build_auxiliary_provider("openai/gpt-4o-mini") is None


@pytest.mark.asyncio
async def test_the_outcome_records_the_model_that_ran_the_pass(tmp_path, monkeypatch):
    cwd = str(tmp_path)
    monkeypatch.setattr(refine_mod, "resolve_refine_provider", lambda p: (p, "openai/gpt-4o-mini"))
    plan = {
        "summary": "Recorded a lesson.",
        "rationale": "the user corrected this twice",
        "edits": [
            {
                "action": "create",
                "kind": "memory",
                "id": "lesson",
                "title": "Lesson",
                "content": "Use pytest.",
            }
        ],
    }

    async def _fake_plan(**kwargs):
        return plan

    monkeypatch.setattr(refine_mod, "plan_refinement", _fake_plan)
    outcome = await run_refinement(
        messages=[UserMessage(content="hi")],
        provider=_mock("session-model"),
        session_id=bridge_session_id(),
        cwd=cwd,
    )

    assert outcome.model == "openai/gpt-4o-mini"
    assert outcome.applied == 1


@pytest.mark.asyncio
async def test_a_rollback_never_routes_to_another_model(tmp_path, monkeypatch):
    """Rollback inverts recorded snapshots without calling a model at all, so
    there is nothing to route — and paying for a provider build would be silly."""
    cwd = str(tmp_path)
    state = _local(cwd)
    state.create("memory", "Lesson", "Use pytest.", id="lesson")
    refine_mod.append_history(
        state,
        {
            "id": "refine_1",
            "summary": "Recorded a lesson.",
            "scope": "local",
            "appliedEdits": [
                {
                    "action": "create",
                    "kind": "memory",
                    "id": "lesson",
                    "applied": True,
                    "before": None,
                    "after": {"title": "Lesson", "content": "Use pytest.", "path": "general"},
                }
            ],
        },
    )
    called = []

    def _record(provider):
        called.append(provider)
        return provider, "should-not-happen"

    monkeypatch.setattr(refine_mod, "resolve_refine_provider", _record)
    outcome = await run_refinement(
        messages=[],
        provider=_mock("session-model"),
        session_id=bridge_session_id(),
        cwd=cwd,
        rollback_id="refine_1",
    )

    assert not called, "rollback must not resolve a refinement model"
    assert outcome.model == ""
    assert outcome.rollback_of == "refine_1"


# =================================================================================================
# stale-entry detection
# =================================================================================================


def test_a_flagged_entry_is_parsed_with_its_scope(tmp_path):
    cwd = str(tmp_path)
    _local(cwd).create("memory", "Port", "The port is 5432.", id="port")
    known = known_entry_index(bridge_session_id(), cwd)

    review = parse_auto_refine_review(
        '{"shouldRefine": false, "rationale": "x", "staleEntries":'
        ' [{"kind": "memory", "id": "port", "reason": "the app now uses 6432"}]}',
        known=known,
    )

    assert len(review.stale_entries) == 1
    assert review.stale_entries[0].id == "port"
    assert review.stale_entries[0].scope == "local"
    assert review.stale_entries[0].reason == "the app now uses 6432"


def test_a_hallucinated_entry_id_is_dropped(tmp_path):
    """The gate is a cheap model; acting on an invented id would have the plan
    pass editing something that does not exist."""
    cwd = str(tmp_path)
    _local(cwd).create("memory", "Port", "The port is 5432.", id="port")
    known = known_entry_index(bridge_session_id(), cwd)

    review = parse_auto_refine_review(
        '{"shouldRefine": false, "rationale": "x", "staleEntries":'
        ' [{"kind": "memory", "id": "imagined", "reason": "because"}]}',
        known=known,
    )

    assert review.stale_entries == []


def test_a_global_entry_is_flaggable_from_a_local_pass(tmp_path):
    """A global entry is asserted to every session, so contradicting it from a
    local pass is exactly the case worth catching."""
    cwd = str(tmp_path)
    get_harness_state(global_=True).create("memory", "Shared", "Wrong.", id="shared")
    known = known_entry_index(bridge_session_id(), cwd)

    review = parse_auto_refine_review(
        '{"shouldRefine": false, "rationale": "x", "staleEntries":'
        ' [{"kind": "memory", "id": "shared", "reason": "now different"}]}',
        known=known,
    )

    assert [e.scope for e in review.stale_entries] == ["global"]


def test_an_unknown_kind_is_dropped(tmp_path):
    cwd = str(tmp_path)
    _local(cwd).create("memory", "Port", "x", id="port")
    known = known_entry_index(bridge_session_id(), cwd)

    review = parse_auto_refine_review(
        '{"shouldRefine": false, "rationale": "x", "staleEntries":'
        ' [{"kind": "nonsense", "id": "port"}]}',
        known=known,
    )

    assert review.stale_entries == []


def test_duplicate_reports_collapse(tmp_path):
    cwd = str(tmp_path)
    _local(cwd).create("memory", "Port", "x", id="port")
    known = known_entry_index(bridge_session_id(), cwd)

    review = parse_auto_refine_review(
        '{"shouldRefine": false, "rationale": "x", "staleEntries":'
        ' [{"kind": "memory", "id": "port", "reason": "a"},'
        ' {"kind": "memory", "id": "port", "reason": "b"}]}',
        known=known,
    )

    assert len(review.stale_entries) == 1


def test_a_missing_reason_falls_back_to_a_default(tmp_path):
    cwd = str(tmp_path)
    _local(cwd).create("memory", "Port", "x", id="port")
    known = known_entry_index(bridge_session_id(), cwd)

    review = parse_auto_refine_review(
        '{"shouldRefine": false, "rationale": "x", "staleEntries":'
        ' [{"kind": "memory", "id": "port"}]}',
        known=known,
    )

    assert review.stale_entries[0].reason == STALE_REASON


def test_no_stale_key_means_no_flags(tmp_path):
    """A gate that never heard of staleness must not be treated as suspect."""
    review = parse_auto_refine_review('{"shouldRefine": true, "rationale": "x"}')

    assert review.stale_entries == []


@pytest.mark.asyncio
async def test_a_contradiction_alone_triggers_a_pass(tmp_path, monkeypatch):
    """Nothing else would ever correct a wrong entry, so a flag is a reason to
    refine even when the gate declined to add anything new."""
    from vtx.ai.agent.config import HarnessConfig, set_harness_config

    cwd = str(tmp_path)
    _local(cwd).create("memory", "Port", "The port is 5432.", id="port")
    # Interval 1 so the gate is actually consulted on this call.
    set_harness_config(HarnessConfig(auto_refine_turn_interval=1))
    agent = _agent_with_harness(cwd)
    # `run()` normally bumps this at the boundary; calling the gate directly
    # has to do it by hand or the interval check short-circuits.
    agent._auto_refine_turns_since_review = 1
    review = AutoRefineReview(
        should_refine=False,
        rationale="the config moved to 6432",
        stale_entries=[
            refine_mod.StaleEntry(kind="memory", id="port", scope="local", reason="now 6432")
        ],
    )

    async def _review(**kwargs):
        return review

    monkeypatch.setattr(refine_mod, "review_auto_refine", _review)
    runs = []

    async def _run(**kwargs):
        runs.append(kwargs)
        return RefinementOutcome(id="r1", summary="s", applied=1, total=1, scope="local")

    monkeypatch.setattr(refine_mod, "run_refinement", _run)
    await agent._maybe_auto_refine("turn_interval", None)

    assert runs, "a flagged contradiction must schedule a pass"
    assert "port" in runs[0]["instructions"]
    assert "now 6432" in runs[0]["instructions"]


def test_the_stale_instructions_ask_for_a_correction_not_a_second_entry(tmp_path):
    review = AutoRefineReview(
        should_refine=False,
        rationale="observed the new port",
        stale_entries=[
            refine_mod.StaleEntry(kind="memory", id="port", scope="local", reason="now 6432")
        ],
    )

    text = stale_instructions(review)

    assert "contradicted" in text
    assert "do not add a second entry" in text
    assert "[local:port] memory" in text
    assert "now 6432" in text


def test_a_gate_with_no_flags_asks_for_nothing_extra():
    assert stale_instructions(AutoRefineReview(should_refine=False, rationale="quiet"))


def test_the_review_prompt_asks_for_contradictions():
    prompt = refine_mod.AUTO_REFINE_REVIEW_SYSTEM_PROMPT

    assert "staleEntries" in prompt
    assert "now contradicts" in prompt
    assert "never re-checked once written" in prompt


def test_a_correct_but_unused_entry_is_not_stale():
    """The prompt has to keep the gate from flagging everything merely unused,
    or every pass would start rewriting entries that were fine."""
    prompt = refine_mod.AUTO_REFINE_REVIEW_SYSTEM_PROMPT

    assert "A correct-but-unused entry is not stale" in prompt


@pytest.mark.asyncio
async def test_review_resolves_stale_ids_against_the_live_store(tmp_path):
    """An id the store does not have must not survive into the review."""
    cwd = str(tmp_path)
    _local(cwd).create("memory", "Port", "x", id="port")
    provider = _JsonProvider(
        {
            "shouldRefine": False,
            "rationale": "nothing to add",
            "staleEntries": [
                {"kind": "memory", "id": "port", "reason": "moved"},
                {"kind": "memory", "id": "invented", "reason": "moved"},
            ],
        }
    )

    review = await review_auto_refine(
        messages=[UserMessage(content="hi")],
        provider=provider,
        states=(_local(cwd),),
        history=[],
        reason=AUTO_REFINE_REASON_TURN_INTERVAL,
        turns_since_last_review=25,
        session_id=bridge_session_id(),
        cwd=cwd,
    )

    assert [e.id for e in review.stale_entries] == ["port"]


def _agent_with_harness(cwd: str) -> Agent:
    return Agent(MockProvider(), [], Session.in_memory(cwd=cwd), cwd=cwd)

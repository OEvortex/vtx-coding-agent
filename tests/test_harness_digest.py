"""Tests for the continual-harness digest: relevance ranking, scope-aware
merge, state fingerprinting, cold-boundary delivery, and the context/output
budgeting the refinement calls run under.

These cover the behaviours the digest has to get right for refinement to be
worth anything: a relevant entry has to survive the per-kind display limit, a
colliding local entry must not hide the global one, and the model must not be
re-sent an equivalent digest on every turn.
"""

import asyncio

import pytest

from vtx.ai.agent.loop import Agent
from vtx.ai.agent.rlm import refine as refine_mod
from vtx.ai.agent.rlm.harness import get_harness_state, harness_query_terms
from vtx.ai.agent.rlm.refine import (
    MODE_CODE_FIRST,
    MODE_TOOL_FIRST,
    build_digest_query_terms,
    create_harness_digest_message,
    delivered_digest_fingerprint,
    harness_digest_for_prompt,
    harness_digest_with_fingerprint,
    harness_refinement_malformation,
    load_merged_harness,
    merge_refinement_history,
    refinement_request,
)
from vtx.ai.agent.rlm.registry import bridge_session_id, reset_state
from vtx.ai.agent.session import Session
from vtx.ai.base import LLMStream
from vtx.ai.providers.mock import MockProvider
from vtx.core.types import (
    AssistantMessage,
    StopReason,
    StreamDone,
    TextContent,
    TextPart,
    ToolResultMessage,
    UserMessage,
)


@pytest.fixture(autouse=True)
def clean_state():
    reset_state()
    yield
    reset_state()


def _local(cwd):
    return get_harness_state(refine_mod.local_state_dir(bridge_session_id(), cwd))


# =================================================================================================
# relevance ranking
# =================================================================================================


def test_ranking_surfaces_relevant_entry_past_the_display_limit(tmp_path):
    """The whole point of ranking: an entry sorted past the cap is still visible."""
    cwd = str(tmp_path)
    state = _local(cwd)
    # The one entry that matters sorts last alphabetically.
    state.create("memory", "zzz postgres pooling", "Use pgbouncer on port 6432 for prod.")
    for i in range(refine_mod._DIGEST_ENTRY_LIMIT + 1):
        state.create("memory", f"aaa unrelated note {i}", f"unrelated content {i}")

    unranked = harness_digest_for_prompt(bridge_session_id(), cwd, mode=MODE_CODE_FIRST)
    assert "pgbouncer" not in unranked, "precondition: alphabetical order hides it"

    ranked = harness_digest_for_prompt(
        bridge_session_id(),
        cwd,
        mode=MODE_CODE_FIRST,
        query_terms=build_digest_query_terms(objective="fix the postgres pooling"),
    )
    assert "pgbouncer" in ranked
    # The model is told the list is ranked, so a hidden entry is not mistaken
    # for a nonexistent one.
    assert "ranked by relevance" in ranked
    assert "harness.search" in ranked, "the REPL read path is named for code_first"


def test_query_terms_weight_goal_above_recent_messages():
    terms = build_digest_query_terms(
        objective="postgres pooling",
        messages=[UserMessage(content="postgres pooling and also deployment")],
    )
    assert terms["pooling"] > terms["deployment"]


def test_query_terms_drop_function_words_when_mining():
    """Mined conversation must not turn `the`/`and` into ranking signal."""
    terms = build_digest_query_terms(
        messages=[UserMessage(content="the connection and the retry should be pooled")]
    )
    assert "the" not in terms and "and" not in terms
    assert "connection" in terms and "retry" in terms
    # An explicit search still accepts short terms.
    assert harness_query_terms("rlm api") == ["rlm", "api"]


def test_ranking_breaks_ties_stably(tmp_path):
    cwd = str(tmp_path)
    state = _local(cwd)
    for name in ("beta", "alpha", "gamma"):
        state.create("memory", name, "shared content")
    terms = build_digest_query_terms(objective="shared")
    first = harness_digest_for_prompt(
        bridge_session_id(), cwd, mode=MODE_CODE_FIRST, query_terms=terms
    )
    second = harness_digest_for_prompt(
        bridge_session_id(), cwd, mode=MODE_CODE_FIRST, query_terms=terms
    )
    # Equal scores must not reshuffle, or the digest churns for nothing.
    assert first == second
    assert first.index("alpha") < first.index("beta") < first.index("gamma")


# =================================================================================================
# scope-aware merge
# =================================================================================================


def test_local_entry_does_not_hide_colliding_global_entry(tmp_path):
    cwd = str(tmp_path)
    get_harness_state(global_=True).create("memory", "Shared id", "GLOBAL deployment runbook.")
    _local(cwd).create("memory", "Shared id", "LOCAL deployment note.")

    digest = harness_digest_for_prompt(bridge_session_id(), cwd, mode=MODE_CODE_FIRST)
    assert "GLOBAL deployment runbook." in digest, "global entry was clobbered"
    assert "LOCAL deployment note." in digest
    # Scope prefixes keep them distinguishable to the model.
    assert "[global:shared_id]" in digest and "[local:shared_id]" in digest


def test_merged_digest_counts_both_scopes(tmp_path):
    cwd = str(tmp_path)
    get_harness_state(global_=True).create("memory", "Shared id", "global")
    _local(cwd).create("memory", "Shared id", "local")
    merged, _ = load_merged_harness(bridge_session_id(), cwd)
    assert len(merged["memory"]) == 2


# =================================================================================================
# fingerprint
# =================================================================================================


def test_fingerprint_tracks_state_not_ranking(tmp_path):
    """Ranking changes the text every turn; state does not."""
    cwd = str(tmp_path)
    state = _local(cwd)
    # Two entries, so ranking can actually reorder them.
    state.create("memory", "zzz postgres pooling", "pgbouncer")
    state.create("memory", "aaa unrelated", "nothing to see")

    base, fingerprint = harness_digest_with_fingerprint(
        bridge_session_id(), cwd, query_terms={"pooling": 3.0}
    )
    other, other_fingerprint = harness_digest_with_fingerprint(
        bridge_session_id(), cwd, query_terms={"unrelated": 3.0}
    )
    assert base != other, "precondition: ranking changed the rendered text"
    assert fingerprint == other_fingerprint, "ranking must not count as state"

    state.update("memory", "zzz_postgres_pooling", "zzz postgres pooling", "pgbouncer on 6433")
    _, changed = harness_digest_with_fingerprint(bridge_session_id(), cwd)
    assert changed != fingerprint


def test_fingerprint_differs_per_mode(tmp_path):
    """The mode selects the prompt lines the digest prints."""
    cwd = str(tmp_path)
    _local(cwd).create("memory", "Pref", "content")
    _, code_first = harness_digest_with_fingerprint(bridge_session_id(), cwd, mode=MODE_CODE_FIRST)
    _, tool_first = harness_digest_with_fingerprint(bridge_session_id(), cwd, mode=MODE_TOOL_FIRST)
    assert code_first != tool_first


def test_no_digest_yields_no_fingerprint(tmp_path):
    assert harness_digest_with_fingerprint(bridge_session_id(), str(tmp_path)) == ("", "")


def test_digest_message_round_trips_its_fingerprint():
    message = create_harness_digest_message("# Continual Harness State", "abc123")
    assert delivered_digest_fingerprint(message) == "abc123"
    assert delivered_digest_fingerprint(UserMessage(content="plain")) is None


def test_malformed_refinement_event_is_skipped_not_raised(tmp_path):
    """One corrupt store element must not break prompt construction."""
    from vtx.ai.agent.rlm.harness import RefinementEvent

    cwd = str(tmp_path)
    state = _local(cwd)
    state.create("memory", "Pref", "content")
    # A hand-edited store can hold a non-string change; the digest joins the
    # list with str.join, which would raise on it.
    state.refinements.append(RefinementEvent(id="bad", trigger="t", changes="not-a-list"))

    digest = harness_digest_for_prompt(bridge_session_id(), cwd, mode=MODE_CODE_FIRST)
    assert "skipped malformed refinement event" in digest
    assert "Pref" in digest, "the healthy entry must still render"


def test_refinement_malformation_names_the_broken_field():
    from vtx.ai.agent.rlm.harness import RefinementEvent

    assert (
        harness_refinement_malformation(RefinementEvent(id="a", trigger="t", changes=[])) is None
    )
    assert "changes" in harness_refinement_malformation(
        RefinementEvent(id="a", trigger="t", changes=[1])
    )
    assert "trigger" in harness_refinement_malformation(
        {"id": "a", "trigger": None, "changes": []}
    )


# =================================================================================================
# merged refinement history
# =================================================================================================


def test_merged_history_shows_global_results_to_a_local_pass():
    """A local pass must not re-learn a lesson another session already tried."""
    merged = merge_refinement_history(
        [{"id": "global_1", "scope": "global", "summary": "tried globally"}],
        [{"id": "local_1", "scope": "local", "summary": "tried here"}],
    )
    assert {item["id"] for item in merged} == {"global_1", "local_1"}


def test_merged_history_prefers_the_session_copy_and_keeps_scope():
    merged = merge_refinement_history(
        [{"id": "x", "scope": "global", "summary": "old"}], [{"id": "x", "summary": "new"}]
    )
    assert len(merged) == 1
    assert merged[0]["summary"] == "new"
    assert merged[0]["scope"] == "global", "session copy must inherit the known scope"


def test_run_refinement_plans_against_global_history(tmp_path, monkeypatch):
    """Wiring check: the plan pass receives global results, not just local ones."""
    cwd = str(tmp_path)
    global_state = get_harness_state(global_=True)
    local_state = _local(cwd)
    # A real run records the result in both the state file and refinements.jsonl;
    # the plan pass reads the latter.
    for state, result_id in ((global_state, "refine_global_1"), (local_state, "refine_local_1")):
        refine_mod.append_history(state, {"id": result_id, "summary": "s", "appliedEdits": []})
        state.record_refinement("tried", ["create memory:x"], id=result_id)

    seen: dict[str, list[dict]] = {}

    async def _plan(**kwargs):
        seen["history"] = kwargs["history"]
        return {"summary": "s", "rationale": "r", "expectedOutcome": "o", "edits": []}

    monkeypatch.setattr(refine_mod, "plan_refinement", _plan)
    asyncio.run(
        refine_mod.run_refinement(
            messages=[UserMessage(content="go")],
            provider=MockProvider(),
            session_id=bridge_session_id(),
            cwd=cwd,
        )
    )
    assert {item["id"] for item in seen["history"]} == {"refine_global_1", "refine_local_1"}


# =================================================================================================
# context / output budgeting
# =================================================================================================


def test_budget_trims_trajectory_to_fit_a_small_context_window():
    conversation = "x" * 500_000
    prompt, max_tokens = refinement_request(
        system_prompt="s" * 2_000,
        conversation=conversation,
        build_prompt=lambda text: f"HEADER\n{text}\nFOOTER",
        output_reserve=32_000,
        context_window=16_000,
        model_max_tokens=8_192,
    )
    assert len(prompt) < len(conversation)
    assert "omitted to fit" in prompt
    # The whole request has to fit, reply included.
    assert len(prompt.encode()) + 2_000 + 1_024 + max_tokens <= 16_000
    assert max_tokens > 0


def test_budget_keeps_the_tail_the_newest_turns():
    conversation = "OLD" + "y" * 200_000 + "NEWEST"
    prompt, _ = refinement_request(
        system_prompt="s",
        conversation=conversation,
        build_prompt=lambda text: text,
        output_reserve=4_000,
        context_window=8_000,
        model_max_tokens=4_000,
    )
    assert prompt.endswith("NEWEST")
    assert "OLD" not in prompt


def test_budget_caps_output_at_the_reserve_not_the_model_ceiling():
    """A small call must not be handed the model's full ceiling."""
    _, max_tokens = refinement_request(
        system_prompt="s",
        conversation="",
        build_prompt=lambda _: "p",
        output_reserve=4_096,
        context_window=200_000,
        model_max_tokens=64_000,
    )
    assert max_tokens == 4_096


def test_budget_names_the_case_where_no_reply_can_fit():
    with pytest.raises(ValueError, match="no room for output"):
        refinement_request(
            system_prompt="s" * 2_000,
            conversation="",
            build_prompt=lambda _: "y" * 9_000,
            output_reserve=32_000,
            context_window=8_000,
            model_max_tokens=4_000,
        )


class _TruncatingProvider:
    """Provider whose stream stops for output budget, capturing the request.

    The payload is *balanced* JSON on purpose: only the stop reason reveals that
    the reply was cut short, so a test that shipped a mid-value fragment would
    pass through the JSON extractor's own truncation diagnosis and prove nothing
    about the stop-reason check.
    """

    model = "test/truncating"

    def __init__(self, payload='{"summary": "s", "edits": [], "rationale": "r"}'):
        self.config = None
        self.max_tokens = None
        self.prompt = None
        self.payload = payload

    async def stream(self, messages, *, system_prompt=None, tools=None, **kwargs):
        self.max_tokens = kwargs.get("max_tokens")
        self.prompt = messages[0].content
        stream = LLMStream()

        async def gen():
            yield TextPart(text=self.payload)
            yield StreamDone(stop_reason=StopReason.LENGTH)

        stream.set_iterator(gen())
        return stream


def test_length_stop_is_reported_as_truncation_not_parsed(tmp_path):
    """A budget-stopped stream is refused even when the text happens to parse."""
    state = _local(str(tmp_path))

    with pytest.raises(ValueError, match="output budget"):
        asyncio.run(
            refine_mod.plan_refinement(
                messages=[UserMessage(content="work")],
                provider=_TruncatingProvider(),
                states=(state,),
                history=[],
            )
        )


def test_review_gate_budgets_its_request_too(tmp_path, monkeypatch):
    state = _local(str(tmp_path))
    provider = _TruncatingProvider()
    # A window small enough that fitting has to drop trajectory: the gate
    # inherits the plan pass's fitting rather than sending an oversized request.
    monkeypatch.setattr(refine_mod, "_model_limits", lambda _p: (8_000, 4_000))

    with pytest.raises(ValueError, match="output budget"):
        asyncio.run(
            refine_mod.review_auto_refine(
                messages=[UserMessage(content="z" * 30_000)],
                provider=provider,
                states=(state,),
                history=[],
                reason="turn_interval",
                turns_since_last_review=3,
            )
        )
    assert provider.max_tokens <= refine_mod.AUTO_REFINE_REVIEW_MAX_OUTPUT_TOKENS
    assert "omitted to fit" in provider.prompt


def test_budget_does_not_split_a_surrogate_pair():
    emoji = "\U0001f600" * 20_000
    prompt, _ = refinement_request(
        system_prompt="s",
        conversation=emoji,
        build_prompt=lambda text: text,
        output_reserve=4_000,
        context_window=8_000,
        model_max_tokens=4_000,
    )
    prompt.encode("utf-8")  # would raise on a lone surrogate
    assert "omitted to fit" in prompt


# =================================================================================================
# cold-boundary delivery
# =================================================================================================


def _agent(cwd, **kwargs):
    return Agent(MockProvider(scenario="simple_text"), [], Session.in_memory(), cwd=cwd, **kwargs)


def test_digest_is_not_rewritten_when_only_the_ranking_would_change(tmp_path):
    """The digest is frozen for a delivery.

    Query terms are mined fresh at every boundary, so re-ranking always produces
    a new rendering. Rewriting the message on that basis would invalidate the
    cached prefix behind it every turn for a change the model cannot act on, so
    only a harness *state* change may rewrite it.
    """
    cwd = str(tmp_path)
    state = _local(cwd)
    # Two entries, so the ranking order can actually move.
    state.create("memory", "aaa postgres pooling", "pgbouncer")
    state.create("memory", "zzz deployment notes", "kubectl rollout")

    agent = _agent(cwd)
    agent._ensure_harness_digest_context()
    entry = agent.session.get_entry(agent._harness_digest_entry_id)
    delivered = entry.message
    first_ranking = delivered.content.index("aaa postgres pooling") < delivered.content.index(
        "zzz deployment notes"
    )
    assert first_ranking, "precondition: the objective entry sorts into view"

    # Same state, different task, so ranking would reorder the two entries.
    monkey = agent
    monkey._goal_objective = lambda: "deployment rollout"
    monkey._ensure_harness_digest_context()

    entry = agent.session.get_entry(agent._harness_digest_entry_id)
    assert entry.message is delivered, "the digest was rewritten on a ranking-only change"
    assert len(agent.session.all_messages) == 1


def test_digest_is_rewritten_in_place_when_state_changes(tmp_path):
    """One slot, refreshed: the append-only log cannot accumulate copies."""
    cwd = str(tmp_path)
    state = _local(cwd)
    state.create("memory", "Pref", "Use pytest.", id="pref")
    agent = _agent(cwd)
    agent._ensure_harness_digest_context()
    original = agent.session.get_entry(agent._harness_digest_entry_id).message

    state.update("memory", "pref", "Pref", "Use pytest -x.")
    agent._ensure_harness_digest_context()

    messages = agent.session.all_messages
    assert len(messages) == 1, "a second digest stacked behind the first"
    assert "Use pytest -x." in messages[0].content
    assert agent.session.get_entry(agent._harness_digest_entry_id).message is not original


def test_subagents_do_not_deliver_their_own_digest(tmp_path):
    cwd = str(tmp_path)
    _local(cwd).create("memory", "Pref", "Use pytest.", id="pref")
    agent = _agent(cwd, depth=1)
    agent._ensure_harness_digest_context()
    assert agent.session.all_messages == []


def test_resumed_digest_is_adopted_not_duplicated(tmp_path):
    cwd = str(tmp_path)
    _local(cwd).create("memory", "Pref", "Use pytest.", id="pref")
    session = Session.in_memory()
    _agent(cwd)  # a prior engine in this session

    digest, fingerprint = harness_digest_with_fingerprint(bridge_session_id(), cwd)
    session.append_message(UserMessage(content="earlier work"))
    session.append_message(create_harness_digest_message(digest, fingerprint))

    agent = _agent(cwd)
    agent.session = session
    agent._ensure_harness_digest_context()

    digests = [
        m for m in agent.session.all_messages if delivered_digest_fingerprint(m) is not None
    ]
    assert len(digests) == 1


def test_delivery_failure_does_not_block_the_turn(tmp_path, monkeypatch):
    agent = _agent(str(tmp_path))
    monkeypatch.setattr(
        refine_mod,
        "harness_digest_with_fingerprint",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("harness unreadable")),
    )
    agent._ensure_harness_digest_context()  # must not raise
    assert agent.session.all_messages == []


def test_digest_is_absent_from_the_system_prompt(tmp_path):
    """It is context, not prompt: folding it in would churn the cached prefix."""
    from vtx.ai.agent.prompts import build_system_prompt

    cwd = str(tmp_path)
    _local(cwd).create("memory", "Pref", "Use pytest.", id="pref")
    assert "# Continual Harness State" not in build_system_prompt(cwd, tools=[])
    assert "# Continual Harness State" in harness_digest_for_prompt(
        bridge_session_id(), cwd, mode=MODE_CODE_FIRST
    )


# =================================================================================================
# conversation serialization still covers every role
# =================================================================================================


def test_serialize_conversation_labels_each_role():
    text = refine_mod.serialize_conversation(
        [
            UserMessage(content="do it"),
            AssistantMessage(content=[TextContent(text="thinking")]),
            ToolResultMessage(
                content=[TextContent(text="result")], tool_call_id="c1", tool_name="bash"
            ),
        ]
    )
    assert "[user]" in text and "[assistant]" in text and "[tool]" in text

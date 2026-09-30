"""Tests for the continual-harness refinement wiring: refine executor, host
notice events, forced compaction, harness digest, and the /refine command."""

import asyncio
import json

import pytest

from vtx.ai.agent.loop import Agent
from vtx.ai.agent.rlm.harness import HarnessState, get_harness_state
from vtx.ai.agent.rlm.refine import (
    MODE_CODE_FIRST,
    MODE_TOOL_FIRST,
    TRUNCATED_JSON_ERROR,
    RefinementOutcome,
    apply_refinement,
    extract_json_object,
    generate_refinement_id,
    harness_digest_for_prompt,
    local_state_dir,
    normalize_proposal,
    parse_refine_command_options,
    rollback_proposal,
    run_refinement,
    snapshot_baseline,
    validate_edit,
)
from vtx.ai.agent.rlm.registry import bridge_session_id, get_registry, reset_state
from vtx.ai.agent.session import Session
from vtx.ai.base import LLMStream
from vtx.ai.providers.mock import MockProvider
from vtx.core import HostNoticeEvent
from vtx.core.events import CompactionEndEvent, CompactionStartEvent
from vtx.core.types import StopReason, TextPart, UserMessage
from vtx.tui.commands.harness import HarnessCommands


@pytest.fixture(autouse=True)
def clean_registry():
    reset_state()
    yield
    reset_state()


# =================================================================================================
# parse_refine_command_options (prime parseRefineCommandOptions parity)
# =================================================================================================


def test_parse_refine_instructions_and_global_leading_flag():
    options = parse_refine_command_options("--global keep the style guide updated")
    assert options.global_ is True
    assert options.instructions == "keep the style guide updated"
    assert options.rollback_id is None
    assert options.errors == []


def test_parse_refine_plain_instructions():
    options = parse_refine_command_options("prefer pytest for tests")
    assert options.instructions == "prefer pytest for tests"
    assert options.global_ is False


def test_parse_refine_empty_args_is_noop():
    options = parse_refine_command_options("")
    assert options.instructions is None
    assert options.errors == []


def test_parse_refine_rollback_missing_id_uses_prime_usage_string():
    options = parse_refine_command_options("rollback")
    assert options.errors == ["Usage: /refine rollback <refinement-id>"]
    assert options.rollback_id is None


def test_parse_refine_rollback_id_with_trailing_global():
    options = parse_refine_command_options("rollback refine_123 --global")
    assert options.rollback_id == "refine_123"
    assert options.global_ is True
    assert options.errors == []


def test_parse_refine_rollback_only_global_flag_is_usage_error():
    options = parse_refine_command_options("rollback --global")
    assert options.errors == ["Usage: /refine rollback <refinement-id>"]


# =================================================================================================
# extract/normalize/validate (prime refinement.ts ports)
# =================================================================================================


def test_generate_refinement_id_format():
    rid = generate_refinement_id()
    assert rid.startswith("refine_")
    assert rid[7:].isdigit()
    assert len(rid) == 7 + 17


def test_extract_json_object_plain_fenced_and_truncated():
    assert extract_json_object('{"edits": []}') == {"edits": []}
    assert extract_json_object('```json\n{"edits": []}\n```') == {"edits": []}
    with pytest.raises(ValueError, match="truncated"):
        extract_json_object('{"edits": [{"action": "create"')
    with pytest.raises(ValueError):
        extract_json_object("no json here")
    assert TRUNCATED_JSON_ERROR


def test_extract_json_object_tolerates_raw_control_chars():
    payload = '{"edits": [{"action": "create", "content": "line one\nline two"}]}'
    assert extract_json_object(payload)["edits"][0]["content"] == "line one\nline two"
    assert extract_json_object(f"Sure!\n{payload}\nDone.")["edits"][0]["action"] == "create"


def test_normalize_proposal_preserves_invalid_fields():
    proposal = normalize_proposal(
        {
            "edits": [
                {"action": "destroy", "kind": "memory", "id": 7, "title": "t", "content": "c"},
                "not-a-dict",
            ]
        }
    )
    assert proposal["edits"][0]["action"] == "destroy"
    assert proposal["edits"][0]["id"] is None
    assert len(proposal["edits"]) == 1
    assert proposal["summary"]  # default summary when missing


def test_validate_edit_rules():
    assert (
        validate_edit({"action": "obliterate", "kind": "memory"})
        == "unsupported action obliterate"
    )
    assert validate_edit({"action": "create", "kind": "diary"}) == "unsupported kind diary"
    assert (
        validate_edit(
            {
                "action": "update",
                "kind": "prompt",
                "id": "base_system_prompt",
                "title": "x",
                "content": "y",
            }
        )
        == "base system prompt is not editable"
    )
    assert validate_edit({"action": "delete", "kind": "memory"}) == "delete requires id"
    assert validate_edit({"action": "create", "kind": "memory", "title": "t"}) == (
        "create requires title and content"
    )
    # skill edits require the reference + arguments contract
    edit = {"action": "create", "kind": "skill", "title": "t", "content": "c"}
    assert validate_edit(dict(edit)) == "create skill requires arguments"
    assert validate_edit({**edit, "arguments": {}}) == "create skill entries require a reference"
    assert validate_edit({**edit, "arguments": {}, "reference": {"type": "shell"}}) == (
        "create skill reference.type must be one of: python, tool_first"
    )
    assert validate_edit({**edit, "arguments": {}, "reference": {"type": "python"}}) == (
        "create skill reference requires a Python import"
    )
    assert (
        validate_edit(
            {**edit, "arguments": {}, "reference": {"type": "python", "import": "pkg.mod"}}
        )
        == "create skill reference requires a callable or call_pattern"
    )
    assert (
        validate_edit(
            {
                **edit,
                "arguments": {},
                "reference": {"type": "python", "import": "pkg.mod", "callable": "run"},
            }
        )
        is None
    )


def test_validate_edit_accepts_tool_first_skill_reference():
    edit = {"action": "create", "kind": "skill", "title": "t", "content": "c", "arguments": {}}

    ok = {**edit, "reference": {"type": "tool_first", "call_pattern": "make lint"}}
    assert validate_edit(ok) is None
    assert (
        validate_edit({**edit, "reference": {"type": "tool_first"}})
        == "create skill reference of type 'tool_first' requires a call_pattern"
    )
    assert (
        validate_edit(
            {**edit, "reference": {"type": "tool_first", "call_pattern": "x", "import": "pkg.mod"}}
        )
        == "create skill reference of type 'tool_first' must not carry a Python import: "
        "the session has no kernel to import into"
    )


def test_tool_first_skill_entry_persists(tmp_path):
    from vtx.ai.agent.rlm.harness import skill_reference_error

    state = _state(tmp_path)
    entry = state.upsert(
        "skill",
        title="Release notes",
        content="Summarize the last tag.",
        id="rel",
        reference={"type": "tool_first", "call_pattern": "git log --oneline -20"},
        arguments={},
    )
    assert entry.reference["type"] == "tool_first"
    assert skill_reference_error(entry.reference) is None

    with pytest.raises(ValueError, match="must be one of"):
        state.upsert(
            "skill", title="Bad", content="c", id="bad", reference={"type": "shell"}, arguments={}
        )


# =================================================================================================
# apply / rollback against a real HarnessState
# =================================================================================================


def _state(tmp_path) -> HarnessState:
    return HarnessState(tmp_path / "harness_state.json", scope="local")


def test_apply_refinement_per_edit_errors_and_changes(tmp_path):
    state = _state(tmp_path)
    state.create("memory", "Existing", "old content", id="existing")

    proposal = {
        "summary": "mixed bag",
        "rationale": "evidence",
        "expectedOutcome": "better",
        "edits": [
            # happy create
            {
                "action": "create",
                "kind": "memory",
                "id": "fresh",
                "title": "Fresh",
                "content": "new",
            },
            # duplicate create -> entry already exists
            {
                "action": "create",
                "kind": "memory",
                "id": "existing",
                "title": "Dup",
                "content": "x",
            },
            # update of missing entry -> entry not found
            {"action": "update", "kind": "memory", "id": "ghost", "title": "G", "content": "c"},
            # delete of missing entry -> entry not found
            {"action": "delete", "kind": "memory", "id": "ghost2"},
            # invalid action captured per-edit, not raised
            {"action": "explode", "kind": "memory"},
            # delete happy path
            {"action": "delete", "kind": "memory", "id": "existing"},
        ],
    }
    result = apply_refinement(state, proposal, id="refine_1", scope="local")

    by_action = [
        (e.get("action"), e.get("id"), e.get("applied"), e.get("error"))
        for e in result["appliedEdits"]
    ]
    assert by_action[0][2] is True
    assert by_action[1][3] == "entry already exists"
    assert by_action[2][3] == "entry not found"
    assert by_action[3][3] == "entry not found"
    assert by_action[4][3] == "unsupported action explode"
    assert by_action[5][2] is True

    assert state.get("memory", "fresh") is not None
    assert state.get("memory", "existing") is None

    # recorded unconditionally with the applied changes list
    assert len(state.refinements) == 1
    event = state.refinements[0]
    assert event.id == "refine_1"
    assert event.changes == ["create memory:fresh", "delete memory:existing"]
    assert event.evidence == "evidence"


def test_apply_refinement_baseline_conflict(tmp_path):
    state = _state(tmp_path)
    state.create("memory", "Title", "v1", id="m")
    baseline = snapshot_baseline(state)

    # another writer (the kernel) changes the entry while the LLM plans
    state.update("memory", "m", "Title", "v2")

    proposal = {
        "summary": "conflict test",
        "rationale": "r",
        "expectedOutcome": "o",
        "edits": [
            {
                "action": "update",
                "kind": "memory",
                "id": "m",
                "title": "Title",
                "content": "planned",
            }
        ],
    }
    result = apply_refinement(state, proposal, id="refine_2", scope="local", baseline=baseline)
    assert result["appliedEdits"][0]["applied"] is False
    assert result["appliedEdits"][0]["error"] == "entry changed during refinement planning"
    assert state.get("memory", "m").content == "v2"  # untouched


def test_rollback_proposal_inverts_applied_edits(tmp_path):
    state = _state(tmp_path)
    state.create("prompt", "Note", "original", id="keep")
    state.create("prompt", "Temp", "scratch", id="temp")

    proposal = {
        "summary": "setup",
        "rationale": "",
        "expectedOutcome": "",
        "edits": [
            # update -> rollback restores the original content
            {"action": "update", "kind": "prompt", "id": "keep", "title": "Note", "content": "v2"},
            # create -> rollback deletes the entry
            {"action": "create", "kind": "prompt", "id": "new", "title": "New", "content": "x"},
            # delete -> rollback re-creates from the snapshot
            {"action": "delete", "kind": "prompt", "id": "temp"},
        ],
    }
    applied = apply_refinement(state, proposal, id="refine_3", scope="local")
    assert all(e["applied"] for e in applied["appliedEdits"])
    assert state.get("prompt", "keep").content == "v2"
    assert state.get("prompt", "new") is not None
    assert state.get("prompt", "temp") is None

    inverse = rollback_proposal(applied)
    # reverse order: delete undo (re-create), create undo (delete), update undo (restore)
    assert [e["action"] for e in inverse["edits"]] == ["create", "delete", "update"]
    by_action = {e["action"]: e for e in inverse["edits"]}
    assert by_action["update"]["content"] == "original"
    assert by_action["create"]["content"] == "scratch"

    result = apply_refinement(state, inverse, id="refine_4", scope="local")
    assert all(e["applied"] for e in result["appliedEdits"])
    assert state.get("prompt", "keep").content == "original"
    assert state.get("prompt", "new") is None
    assert state.get("prompt", "temp").content == "scratch"


# =================================================================================================
# run_refinement end-to-end (local scope) + jsonl history + rollback
# =================================================================================================


class _JsonProvider:
    """Minimal provider returning one JSON payload as a text stream."""

    def __init__(self, payload):
        self.payload = payload
        self.system_prompt = None

    async def stream(self, messages, *, system_prompt=None, tools=None, **kwargs):
        self.system_prompt = system_prompt
        self.messages = messages
        stream = LLMStream()

        async def gen():
            yield TextPart(text=json.dumps(self.payload))

        stream.set_iterator(gen())
        return stream


def test_run_refinement_local_scope_creates_entry_and_notice(tmp_path):
    session_id = bridge_session_id()
    cwd = str(tmp_path / "work")
    provider = _JsonProvider(
        {
            "summary": "Remember test preference",
            "rationale": "User corrected test framework twice",
            "expectedOutcome": "Tests written with pytest",
            "edits": [
                {
                    "action": "create",
                    "kind": "memory",
                    "id": "test_pref",
                    "title": "Test preference",
                    "content": "Use pytest for all tests.",
                }
            ],
        }
    )

    outcome = asyncio.run(
        run_refinement(
            messages=[UserMessage(content="use pytest please")],
            provider=provider,
            session_id=session_id,
            cwd=cwd,
            source="self",
        )
    )

    assert isinstance(outcome, RefinementOutcome)
    assert outcome.applied == 1
    assert outcome.scope == "local"
    assert outcome.notice is not None
    assert outcome.notice.startswith("[self-refinement]\n\n")
    assert "create memory [local:test_pref] Test preference" in outcome.notice

    # entry landed in the session-local store
    local = get_harness_state(local_state_dir(session_id, cwd))
    assert local.get("memory", "test_pref") is not None

    # the plan pass saw the refinement system prompt and a user message
    assert provider.system_prompt is not None
    assert "continual harness" in provider.system_prompt

    # jsonl history persisted next to the state file
    history_file = local.file_path.parent / "refinements.jsonl"
    assert history_file.exists()
    lines = [json.loads(line) for line in history_file.read_text().splitlines() if line.strip()]
    assert lines[-1]["id"] == outcome.id
    assert lines[-1]["appliedEdits"][0]["applied"] is True


def test_run_refinement_zero_edits_suppresses_notice(tmp_path):
    provider = _JsonProvider(
        {"summary": "Nothing to do", "rationale": "", "expectedOutcome": "", "edits": []}
    )
    outcome = asyncio.run(
        run_refinement(
            messages=[UserMessage(content="hello")],
            provider=provider,
            session_id=bridge_session_id(),
            cwd=str(tmp_path / "work"),
            source="self",
        )
    )
    assert outcome.applied == 0
    assert outcome.notice is None


def test_run_refinement_rollback_without_llm(tmp_path):
    session_id = bridge_session_id()
    cwd = str(tmp_path / "work")
    provider = _JsonProvider(
        {
            "summary": "first pass",
            "rationale": "",
            "expectedOutcome": "",
            "edits": [
                {
                    "action": "create",
                    "kind": "prompt",
                    "id": "style",
                    "title": "Style",
                    "content": "concise",
                }
            ],
        }
    )
    first = asyncio.run(
        run_refinement(
            messages=[UserMessage(content="make tests concise")],
            provider=provider,
            session_id=session_id,
            cwd=cwd,
            source="user",
        )
    )
    assert first.applied == 1

    rollback_provider = _JsonProvider({"edits": []})  # must NOT be called
    outcome = asyncio.run(
        run_refinement(
            messages=[],
            provider=rollback_provider,
            session_id=session_id,
            cwd=cwd,
            source="user",
            rollback_id=first.id,
        )
    )
    assert outcome.rollback_of == first.id
    local = get_harness_state(local_state_dir(session_id, cwd))
    assert local.get("prompt", "style") is None

    # unknown rollback id raises prime's message
    with pytest.raises(ValueError, match="not found"):
        asyncio.run(
            run_refinement(
                messages=[],
                provider=rollback_provider,
                session_id=session_id,
                cwd=cwd,
                rollback_id="refine_missing",
            )
        )


# =================================================================================================
# harness digest
# =================================================================================================


def test_harness_digest_empty_is_omitted(tmp_path):
    digest = harness_digest_for_prompt(bridge_session_id(), str(tmp_path / "nowhere"))
    assert digest == ""


def test_harness_digest_renders_entries_and_refinements(tmp_path):
    session_id = bridge_session_id()
    cwd = str(tmp_path / "work")
    local = get_harness_state(local_state_dir(session_id, cwd))
    local.create("memory", "Test preference", "Use pytest for all tests.", id="test_pref")
    local.create("subagent", "Reviewer", "Review diffs.", id="reviewer")
    local.record_refinement(
        "Remember test preference",
        ["create memory:test_pref"],
        evidence="user corrected twice",
        outcome="tests use pytest",
        id="refine_0001",
    )

    for mode in (MODE_CODE_FIRST, MODE_TOOL_FIRST):
        digest = harness_digest_for_prompt(session_id, cwd, mode=mode)
        assert digest.startswith("# Continual Harness State")
        assert (
            "- [local:test_pref] Test preference (general, v1): Use pytest for all tests."
            in digest
        )
        assert "memory: 1" in digest
        assert "recent refinements: 1" in digest
        assert "- [refine_0001] Remember test preference: create memory:test_pref" in digest


def test_harness_digest_call_contract_is_mode_specific(tmp_path):
    session_id = bridge_session_id()
    cwd = str(tmp_path / "work")
    local = get_harness_state(local_state_dir(session_id, cwd))
    local.create("subagent", "Reviewer", "Review diffs.", id="reviewer")

    rlm_digest = harness_digest_for_prompt(session_id, cwd, mode=MODE_CODE_FIRST)
    assert "await refine.run()" in rlm_digest
    assert "await rlm.spawn" in rlm_digest

    tool_digest = harness_digest_for_prompt(session_id, cwd, mode=MODE_TOOL_FIRST)
    assert "refine` tool" in tool_digest
    assert "`task` tool" in tool_digest
    # The tool-first model has no REPL, so the RLM-native trigger must not leak.
    assert "await refine.run()" not in tool_digest


def test_harness_digest_defaults_to_config_mode(tmp_path, monkeypatch):
    from vtx.ai.agent.rlm import refine as refine_mod

    monkeypatch.setattr(refine_mod, "current_mode", lambda: MODE_TOOL_FIRST)
    cwd = str(tmp_path / "work")
    local = get_harness_state(local_state_dir(bridge_session_id(), cwd))
    local.create("memory", "Test preference", "Use pytest.", id="test_pref")

    assert "refine` tool" in harness_digest_for_prompt(bridge_session_id(), cwd)


# =================================================================================================
# loop wiring: host notices + forced compaction + refinement drain
# =================================================================================================


def test_drain_background_notifications_delivers_host_notices():
    agent = Agent(MockProvider(), [], Session.in_memory())
    registry = get_registry(bridge_session_id())
    registry.add_notice("bash", 1, "[bash-done pid:1 exit:0]\n\necho hi")

    events = agent._drain_background_notifications()
    notices = [e for e in events if isinstance(e, HostNoticeEvent)]
    assert len(notices) == 1
    assert notices[0].kind == "notice"
    assert "[bash-done pid:1 exit:0]" in notices[0].text

    last = agent.session.all_messages[-1]
    assert isinstance(last, UserMessage)
    assert "[bash-done pid:1 exit:0]" in last.content

    # notices drain exactly once even with no background manager
    assert agent._drain_background_notifications() == []


@pytest.mark.asyncio
async def test_check_compaction_forced_by_kernel_request():
    agent = Agent(MockProvider(scenario="simple_text"), [], Session.in_memory())
    registry = get_registry(bridge_session_id())
    registry.compact_pending = {"instructions": "keep the pytest details"}

    events = [event async for event in agent._check_compaction(StopReason.STOP, "system", None)]
    assert any(isinstance(e, CompactionStartEvent) for e in events)
    assert any(isinstance(e, CompactionEndEvent) for e in events)
    assert registry.compact_pending is None

    # the focus hint reached the summarization provider
    sent = [
        m.content
        for m in agent.provider._last_messages
        if isinstance(m, UserMessage) and isinstance(m.content, str)
    ]
    assert any("keep the pytest details" in text for text in sent)


@pytest.mark.asyncio
async def test_check_compaction_not_forced_without_overflow():
    agent = Agent(MockProvider(scenario="simple_text"), [], Session.in_memory())
    events = [event async for event in agent._check_compaction(StopReason.STOP, "system", None)]
    # no usage, no kernel request -> no compaction
    assert events == []


@pytest.mark.asyncio
async def test_drain_pending_refinement_success_and_error(monkeypatch):
    from vtx.ai.agent.rlm import refine as refine_mod

    agent = Agent(MockProvider(), [], Session.in_memory())
    registry = get_registry(bridge_session_id())

    # success with applied edits -> notice appended + reload_context called
    registry.refine_pending = {"instructions": None, "global": None}
    reloads = []
    monkeypatch.setattr(agent, "reload_context", lambda: reloads.append(True))

    async def _ok(**kwargs):
        return RefinementOutcome(
            id="refine_ok",
            summary="tightened prompt",
            applied=1,
            total=1,
            scope="local",
            notice="[self-refinement]\n\n- update prompt [local:note] Note: tighter",
        )

    monkeypatch.setattr(refine_mod, "run_refinement", _ok)
    events = await agent._drain_pending_refinement(None)
    assert len(events) == 1
    assert isinstance(events[0], HostNoticeEvent)
    assert events[0].kind == "refinement"
    assert isinstance(agent.session.all_messages[-1], UserMessage)
    assert agent.session.all_messages[-1].content.startswith("[self-refinement]")
    assert reloads == [True]
    assert registry.refine_in_flight is False

    # failure -> error notice, no crash
    registry.refine_pending = {"instructions": None, "global": None}

    async def _explode(**kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(refine_mod, "run_refinement", _explode)
    events = await agent._drain_pending_refinement(None)
    assert len(events) == 1
    assert events[0].kind == "refinement_error"
    assert "provider down" in events[0].text
    assert registry.refine_in_flight is False


@pytest.mark.asyncio
async def test_drain_pending_refinement_requeues_on_cancel():
    agent = Agent(MockProvider(), [], Session.in_memory())
    registry = get_registry(bridge_session_id())
    registry.refine_pending = {"instructions": "later", "global": False}

    cancel = asyncio.Event()
    cancel.set()
    events = await agent._drain_pending_refinement(cancel)
    assert events == []
    assert registry.refine_pending == {"instructions": "later", "global": False}


# =================================================================================================
# /refine command mixin
# =================================================================================================


class _ChatStub:
    def __init__(self):
        self.infos = []
        self.errors = []
        self.statuses = []
        self.spinners = []

    def add_info_message(self, message, error=False, warning=False):
        (self.errors if error else self.infos).append(message)

    def show_status(self, message):
        self.statuses.append(message)

    def show_spinner_status(self, message):
        self.spinners.append(message)


class _StubHarness(HarnessCommands):
    def __init__(self, chat, runtime, running=False):
        self._chat = chat
        self._runtime = runtime
        self._is_running = running
        self.workers = []

    def query_one(self, selector, widget_type=None):
        return self._chat

    def run_worker(self, coro, exclusive=False):
        self.workers.append(coro)
        coro.close()  # not executed in unit tests


def _runtime_stub():
    class _RT:
        provider = object()
        session = None
        cwd = "/work"

    rt = _RT()
    rt.session = type("S", (), {"all_messages": [], "append_message": lambda self, m: None})()
    return rt


def test_refine_command_usage_error_shown():
    stub = _StubHarness(_ChatStub(), _runtime_stub())
    stub._handle_refine_command("rollback")
    assert stub._chat.errors == ["Usage: /refine rollback <refinement-id>"]
    assert stub.workers == []


def test_refine_command_requires_initialized_agent():
    rt = _runtime_stub()
    rt.provider = None
    stub = _StubHarness(_ChatStub(), rt)
    stub._handle_refine_command("do the thing")
    assert stub._chat.errors == ["Agent not initialized"]


def test_refine_command_queues_while_running():
    stub = _StubHarness(_ChatStub(), _runtime_stub(), running=True)
    stub._handle_refine_command("--global keep style updated")
    registry = get_registry(bridge_session_id())
    assert registry.refine_pending == {
        "instructions": "keep style updated",
        "global": True,
        "rollbackId": None,
    }
    assert stub.workers == []
    assert stub._chat.infos  # scheduled message

    registry.refine_pending = None
    stub._handle_refine_command("rollback refine_abc --global")
    assert registry.refine_pending == {
        "instructions": None,
        "global": True,
        "rollbackId": "refine_abc",
    }


def test_refine_command_not_running_starts_worker():
    stub = _StubHarness(_ChatStub(), _runtime_stub(), running=False)
    stub._handle_refine_command("tighten style")
    assert len(stub.workers) == 1
    assert stub._chat.spinners == ["Refining continual harness..."]

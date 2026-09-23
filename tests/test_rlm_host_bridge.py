"""Tests for the parent-side RLM host bridge (dispatch_host_request + registry).

Spawning is mocked: children run through a fake sub-agent runner scheduled on a
real BackgroundTaskManager under the test's isolated HOME, so no real agent run
ever happens.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from vtx.ai.agent.background import BackgroundTaskManager
from vtx.ai.agent.dispatcher import DispatcherContext, set_context
from vtx.ai.agent.rlm import host
from vtx.ai.agent.rlm import registry as reg
from vtx.ai.agent.rlm.host import dispatch_host_request
from vtx.ai.agent.rlm.registry import drain_notices, drain_pending_compact, drain_pending_refine

SID = "parent-session"

REFINE_NOTE = (
    "Refinement runs when the current turn ends; applied edits are appended "
    "to your context as a refinement notice and you resume automatically. "
    "Continue working normally."
)
COMPACT_NOTE = (
    "Compaction runs when the current turn ends; you resume automatically "
    "afterwards. Continue working normally."
)
LIST_AGENTS_REMOVED = (
    "agent_message.list_agents was removed; the family roster now lives in "
    "agent_observe.list_agents(). Restart the Python kernel to load the "
    "current skills, then call await agent_observe.list_agents()."
)


@pytest.fixture(autouse=True)
def _clean_state():
    reg.reset_state()
    set_context(None)
    yield
    reg.reset_state()
    set_context(None)


@pytest.fixture
def dispatcher(monkeypatch, tmp_path):
    """Install a parent runtime context with a real background manager."""
    manager = BackgroundTaskManager(store_dir=tmp_path / "tasks")
    ctx = DispatcherContext(
        provider=None,
        model="test-model",
        model_provider="test-provider",
        base_url=None,
        thinking_level=None,
        agent_registry=None,
        cwd=str(tmp_path),
        background_manager=manager,
    )
    set_context(ctx)
    return ctx


def install_fake_runner(monkeypatch, **result_kwargs):
    """Replace the sub-agent runner so ``rlm.run`` never launches a real agent."""

    async def runner(**kwargs):
        return SimpleNamespace(
            final_text=result_kwargs.get("final_text", "42"),
            transcript=result_kwargs.get("transcript", ["  → read file"]),
            duration_ms=result_kwargs.get("duration_ms", 10),
            error=result_kwargs.get("error"),
            turns=1,
            usage=None,
        )

    monkeypatch.setattr(host, "_load_runner", lambda: runner)


def spawn_record(reply) -> reg.ChildRecord:
    child_id = reply["result"]["rlm_child_id"]
    return reg.get_registry(SID).children[child_id]


async def settle(ctx, record):
    await ctx.background_manager.wait(record.task_id, timeout=5, cancel_event=None)


# =================================================================================================
# tool.call
# =================================================================================================


@pytest.mark.asyncio
async def test_tool_call_returns_result_envelope():
    async def executor(name, args):
        return {"tool": name, "echo": args}

    reply = await dispatch_host_request(
        {"type": "tool.call", "name": "read", "args": {"path": "/x"}},
        tool_executor=executor,
        session_id=SID,
    )
    assert reply == {"status": "ok", "result": {"tool": "read", "echo": {"path": "/x"}}}


@pytest.mark.asyncio
async def test_tool_call_executor_failure_degrades_to_error_envelope():
    async def executor(name, args):
        raise RuntimeError("boom")

    reply = await dispatch_host_request(
        {"type": "tool.call", "name": "read", "args": {}}, tool_executor=executor, session_id=SID
    )
    assert reply == {"status": "error", "error": "boom"}


@pytest.mark.asyncio
async def test_tool_call_without_executor_is_honest():
    reply = await dispatch_host_request({"type": "tool.call", "name": "read"}, session_id=SID)
    assert reply == {
        "status": "error",
        "error": "tool.call requires a tool executor in this session",
    }


@pytest.mark.asyncio
async def test_tool_call_validates_name_and_args():
    reply = await dispatch_host_request({"type": "tool.call"}, session_id=SID)
    assert reply["error"] == "tool.call name must be a non-empty string"

    async def executor(name, args):
        return {}

    reply = await dispatch_host_request(
        {"type": "tool.call", "name": "read", "args": ["not", "an", "object"]},
        tool_executor=executor,
        session_id=SID,
    )
    assert reply["error"] == "tool.call args must be an object"


@pytest.mark.asyncio
async def test_tool_call_non_serializable_result_reports_honestly():
    circular: dict = {}
    circular["self"] = circular

    async def executor(name, args):
        return circular

    reply = await dispatch_host_request(
        {"type": "tool.call", "name": "circ", "args": {}}, tool_executor=executor, session_id=SID
    )
    assert reply["status"] == "error"
    assert reply["error"].startswith('tool.call result for "circ" is not JSON-serializable')


# =================================================================================================
# unknown types / never-raises
# =================================================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("request_type", ["rlm_heartbeat.tick", "mcp.list_tools", "daemon.ping"])
async def test_unknown_type_uses_exact_message(request_type):
    reply = await dispatch_host_request({"type": request_type}, session_id=SID)
    assert reply == {
        "status": "error",
        "error": f'host request type "{request_type}" is not available in this session',
    }


@pytest.mark.asyncio
async def test_internal_exception_never_raises(monkeypatch):
    def explode(session_id):
        raise RuntimeError("registry exploded")

    monkeypatch.setattr(host, "get_registry", explode)
    reply = await dispatch_host_request({"type": "rlm.list_subagents"}, session_id=SID)
    assert reply == {"status": "error", "error": "registry exploded"}


@pytest.mark.asyncio
async def test_dispatch_alias_is_exported():
    assert host.dispatch is dispatch_host_request


# =================================================================================================
# bash.completed / bash.consumed
# =================================================================================================


@pytest.mark.asyncio
async def test_bash_notice_round_trip():
    reply = await dispatch_host_request(
        {"type": "bash.completed", "pid": 4242, "command": "ls -la", "exitCode": 0}, session_id=SID
    )
    assert reply == {"status": "ok", "result": None}

    notices = drain_notices(SID)
    assert notices == ['[bash-done pid:4242 exit:0]\n\nCommand: "ls -la"']
    assert drain_notices(SID) == []

    # Re-queue, then withdraw by (pid, command).
    await dispatch_host_request(
        {"type": "bash.completed", "pid": 4242, "command": "ls -la", "exitCode": 0}, session_id=SID
    )
    reply = await dispatch_host_request(
        {"type": "bash.consumed", "pid": 4242, "command": "ls -la"}, session_id=SID
    )
    assert reply == {"status": "ok", "result": None}
    assert drain_notices(SID) == []


@pytest.mark.asyncio
async def test_bash_validation_strings():
    reply = await dispatch_host_request(
        {"type": "bash.completed", "pid": 0, "command": "x", "exitCode": 0}, session_id=SID
    )
    assert reply["error"] == "bash.completed pid must be a positive integer"

    reply = await dispatch_host_request(
        {"type": "bash.completed", "pid": 1, "command": "", "exitCode": 0}, session_id=SID
    )
    assert reply["error"] == "bash.completed command must be a non-empty string"

    reply = await dispatch_host_request(
        {"type": "bash.completed", "pid": 1, "command": "x", "exitCode": "0"}, session_id=SID
    )
    assert reply["error"] == "bash.completed exitCode must be an integer"

    reply = await dispatch_host_request(
        {"type": "bash.consumed", "pid": -1, "command": "x"}, session_id=SID
    )
    assert reply["error"] == "bash.consumed pid must be a positive integer"

    reply = await dispatch_host_request(
        {"type": "bash.consumed", "pid": 1, "command": ""}, session_id=SID
    )
    assert reply["error"] == "bash.consumed command must be a non-empty string"


# =================================================================================================
# rlm.run / list / collect / delete / progress.note
# =================================================================================================


@pytest.mark.asyncio
async def test_spawn_list_collect_lifecycle(dispatcher, monkeypatch):
    install_fake_runner(monkeypatch, final_text="42", transcript=["  → read file"])

    reply = await dispatch_host_request(
        {"type": "rlm.run", "prompt": "compute it", "kwargs": {"name": "helper"}}, session_id=SID
    )
    assert reply["status"] == "ok"
    handle = reply["result"]
    assert {"rlm_child_id", "name", "session_dir", "model"} <= set(handle)
    assert handle["name"] == "helper"
    assert handle["model"] == "test-model"

    record = spawn_record(reply)
    await settle(dispatcher, record)

    listed = await dispatch_host_request({"type": "rlm.list_subagents"}, session_id=SID)
    rows = listed["result"]["subagents"]
    assert len(rows) == 1
    row = rows[0]
    assert row["rlm_child_id"] == handle["rlm_child_id"]
    assert row["session_name"] == "helper"
    assert row["status"] == "completed"
    assert row["label"] == "helper"
    assert row["answer_preview"] == "42"
    assert row["tool_use_count"] == 1
    assert row["duration_ms"] == 10
    assert row["active_session_id"] is None

    collected = await dispatch_host_request(
        {"type": "rlm.collect", "targets": [handle["rlm_child_id"]]}, session_id=SID
    )
    entry = collected["result"]["results"][0]
    assert entry["status"] == "done"
    assert entry["settled"] is True
    assert entry["answer_preview"] == "42"
    assert entry["tool_use_count"] == 1
    assert entry["duration_ms"] == 10
    assert entry["error"] is None

    # A collect without targets selects every live child.
    all_children = await dispatch_host_request(
        {"type": "rlm.collect", "targets": [], "timeout_ms": 0}, session_id=SID
    )
    assert [e["rlm_child_id"] for e in all_children["result"]["results"]] == [
        handle["rlm_child_id"]
    ]


@pytest.mark.asyncio
async def test_spawn_duplicate_name_uses_exact_error(dispatcher, monkeypatch):
    install_fake_runner(monkeypatch)

    reply = await dispatch_host_request(
        {"type": "rlm.run", "prompt": "one", "kwargs": {"name": "helper"}}, session_id=SID
    )
    assert reply["status"] == "ok"
    await settle(dispatcher, spawn_record(reply))

    reply = await dispatch_host_request(
        {"type": "rlm.run", "prompt": "two", "kwargs": {"name": "helper"}}, session_id=SID
    )
    assert reply == {
        "status": "error",
        "error": (
            'Agent name "helper" is unavailable: an agent of that name already '
            "exists at depth 1 under this parent"
        ),
    }


@pytest.mark.asyncio
async def test_spawn_validation_strings():
    reply = await dispatch_host_request({"type": "rlm.run", "kwargs": {}}, session_id=SID)
    assert reply["error"] == "rlm.spawn prompt must be a string"

    reply = await dispatch_host_request(
        {"type": "rlm.run", "prompt": "x", "kwargs": {"temperature": 0.5}}, session_id=SID
    )
    assert reply["error"] == "Unsupported rlm.spawn kwargs: temperature"

    reply = await dispatch_host_request(
        {"type": "rlm.run", "prompt": "x", "kwargs": {"model": 5}}, session_id=SID
    )
    assert reply["error"] == "rlm.spawn model must be a string when provided"


@pytest.mark.asyncio
async def test_collect_timeout_returns_running_snapshot(dispatcher, monkeypatch):
    gate = asyncio.Event()

    async def blocking_runner(**kwargs):
        await gate.wait()
        return SimpleNamespace(
            final_text="late", transcript=[], duration_ms=1, error=None, turns=1, usage=None
        )

    monkeypatch.setattr(host, "_load_runner", lambda: blocking_runner)
    try:
        reply = await dispatch_host_request(
            {"type": "rlm.run", "prompt": "wait for it", "kwargs": {"name": "slow"}},
            session_id=SID,
        )
        record = spawn_record(reply)

        started = asyncio.get_running_loop().time()
        collected = await dispatch_host_request(
            {"type": "rlm.collect", "targets": ["slow"], "timeout_ms": 50}, session_id=SID
        )
        assert asyncio.get_running_loop().time() - started < 2.0
        entry = collected["result"]["results"][0]
        assert entry["status"] == "running"
        assert entry["settled"] is False

        gate.set()
        await settle(dispatcher, record)
        collected = await dispatch_host_request(
            {"type": "rlm.collect", "targets": ["slow"]}, session_id=SID
        )
        entry = collected["result"]["results"][0]
        assert entry["status"] == "done"
        assert entry["settled"] is True
        assert entry["answer_preview"] == "late"
    finally:
        gate.set()


@pytest.mark.asyncio
async def test_delete_row_list_exclusion_and_deleted_collect_entry(dispatcher, monkeypatch):
    install_fake_runner(monkeypatch, final_text="done already")

    reply = await dispatch_host_request(
        {"type": "rlm.run", "prompt": "work", "kwargs": {"name": "helper"}}, session_id=SID
    )
    record = spawn_record(reply)
    await settle(dispatcher, record)

    deleted = await dispatch_host_request(
        {"type": "rlm.delete_subagent", "target": "helper"}, session_id=SID
    )
    assert deleted["result"]["subagent"]["session_name"] == "helper"

    listed = await dispatch_host_request({"type": "rlm.list_subagents"}, session_id=SID)
    assert listed["result"]["subagents"] == []

    collected = await dispatch_host_request(
        {"type": "rlm.collect", "targets": ["helper"]}, session_id=SID
    )
    entry = collected["result"]["results"][0]
    assert entry["status"] == "cancelled"
    assert entry["settled"] is True
    assert entry["error"] == "Deleted by parent orchestrator"


@pytest.mark.asyncio
async def test_delete_running_child_cancels_it(dispatcher, monkeypatch):
    gate = asyncio.Event()

    async def blocking_runner(**kwargs):
        await gate.wait()
        return SimpleNamespace(
            final_text="never", transcript=[], duration_ms=1, error=None, turns=1, usage=None
        )

    monkeypatch.setattr(host, "_load_runner", lambda: blocking_runner)
    try:
        reply = await dispatch_host_request(
            {"type": "rlm.run", "prompt": "long job", "kwargs": {"name": "worker"}}, session_id=SID
        )
        record = spawn_record(reply)

        deleted = await dispatch_host_request(
            {"type": "rlm.delete_subagent", "target": "worker"}, session_id=SID
        )
        assert deleted["result"]["subagent"]["status"] == "running"
        assert record.status == "cancelled"
        assert record.deleted is True

        collected = await dispatch_host_request(
            {"type": "rlm.collect", "targets": ["worker"]}, session_id=SID
        )
        entry = collected["result"]["results"][0]
        assert entry["status"] == "cancelled"
        assert entry["settled"] is True
        assert entry["error"] == "Deleted by parent orchestrator"
    finally:
        gate.set()


@pytest.mark.asyncio
async def test_delete_validation_and_no_match_strings(dispatcher):
    reply = await dispatch_host_request(
        {"type": "rlm.delete_subagent", "target": "   "}, session_id=SID
    )
    assert reply["error"] == "rlm.delete_subagent target must be a non-empty string"

    reply = await dispatch_host_request(
        {"type": "rlm.delete_subagent", "target": "ghost"}, session_id=SID
    )
    assert reply["error"] == 'No direct RLM subagent matches "ghost" in the current parent session'


@pytest.mark.asyncio
async def test_collect_no_match_and_validation_strings(dispatcher):
    reply = await dispatch_host_request(
        {"type": "rlm.collect", "targets": ["ghost"]}, session_id=SID
    )
    assert reply["error"] == 'No direct RLM child matches "ghost" in the current parent session'

    reply = await dispatch_host_request(
        {"type": "rlm.collect", "targets": "helper"}, session_id=SID
    )
    assert reply["error"] == "rlm.collect targets must be an array of child ids or names"

    reply = await dispatch_host_request({"type": "rlm.collect", "targets": [""]}, session_id=SID)
    assert reply["error"] == "rlm.collect targets must be non-empty strings"

    reply = await dispatch_host_request(
        {"type": "rlm.collect", "targets": [], "timeout_ms": -1}, session_id=SID
    )
    assert (
        reply["error"] == "rlm.collect timeout_ms must be a non-negative integer up to 2147483647"
    )

    reply = await dispatch_host_request(
        {"type": "rlm.collect", "targets": [], "timeout_ms": 2_147_483_648}, session_id=SID
    )
    assert (
        reply["error"] == "rlm.collect timeout_ms must be a non-negative integer up to 2147483647"
    )


@pytest.mark.asyncio
async def test_progress_note_throttle_and_bounds(dispatcher):
    first = await dispatch_host_request(
        {"type": "rlm.progress.note", "message": "working on it"}, session_id=SID
    )
    assert first == {"status": "ok", "result": {"accepted": True}}

    second = await dispatch_host_request(
        {"type": "rlm.progress.note", "message": "again"}, session_id=SID
    )
    result = second["result"]
    assert result["accepted"] is False
    assert isinstance(result["retry_after_ms"], int)
    assert 1 <= result["retry_after_ms"] <= 10_000

    reply = await dispatch_host_request(
        {"type": "rlm.progress.note", "message": ""}, session_id=SID
    )
    assert reply["error"] == "rlm.progress.note message must be a non-empty string"

    reply = await dispatch_host_request(
        {"type": "rlm.progress.note", "message": "x" * 513}, session_id=SID
    )
    assert reply["error"] == "rlm.progress.note message must be at most 512 characters"

    # Length is measured in UTF-16 code units, like JS String.length.
    reply = await dispatch_host_request(
        {"type": "rlm.progress.note", "message": "\U0001f600" * 257}, session_id=SID
    )
    assert reply["error"] == "rlm.progress.note message must be at most 512 characters"

    # 512 UTF-16 units pass validation and hit the throttle instead.
    reply = await dispatch_host_request(
        {"type": "rlm.progress.note", "message": "\U0001f600" * 256}, session_id=SID
    )
    assert reply["status"] == "ok"
    assert reply["result"]["accepted"] is False


@pytest.mark.asyncio
async def test_progress_note_is_per_registry(dispatcher):
    await dispatch_host_request({"type": "rlm.progress.note", "message": "first"}, session_id=SID)
    other = await dispatch_host_request(
        {"type": "rlm.progress.note", "message": "other registry"}, session_id="other-session"
    )
    assert other == {"status": "ok", "result": {"accepted": True}}


# =================================================================================================
# rlm.find_models / model.info / rlm.create_session
# =================================================================================================


@pytest.mark.asyncio
async def test_find_models_shape_and_limit_string(monkeypatch):
    monkeypatch.setattr(
        host,
        "_find_model_entries",
        lambda query, limit: [{"provider": "p", "id": "m", "name": "m", "selector": "p/m"}][
            :limit
        ],
    )

    reply = await dispatch_host_request(
        {"type": "rlm.find_models", "query": "gpt", "limit": 3}, session_id=SID
    )
    assert reply["result"]["models"][0]["selector"] == "p/m"

    reply = await dispatch_host_request(
        {"type": "rlm.find_models", "query": "", "limit": 0}, session_id=SID
    )
    assert reply["error"] == "rlm.find_models limit must be an integer from 1 to 20"

    reply = await dispatch_host_request(
        {"type": "rlm.find_models", "query": "", "limit": 21}, session_id=SID
    )
    assert reply["error"] == "rlm.find_models limit must be an integer from 1 to 20"

    reply = await dispatch_host_request(
        {"type": "rlm.find_models", "query": "", "limit": True}, session_id=SID
    )
    assert reply["error"] == "rlm.find_models limit must be an integer from 1 to 20"

    reply = await dispatch_host_request({"type": "rlm.find_models", "query": 5}, session_id=SID)
    assert reply["error"] == "rlm.find_models query must be a string"


@pytest.mark.asyncio
async def test_model_info_shape(dispatcher):
    reply = await dispatch_host_request({"type": "model.info"}, session_id=SID)
    assert reply["status"] == "ok"
    info = reply["result"]
    assert set(info) == {"id", "provider", "input"}
    assert info["id"] == "test-model"
    assert info["provider"] == "test-provider"
    assert isinstance(info["input"], list) and "text" in info["input"]


@pytest.mark.asyncio
async def test_create_session_is_admission_free_and_reports_honestly():
    reply = await dispatch_host_request(
        {"type": "rlm.create_session", "prompt": "start a session"}, session_id=SID
    )
    assert reply == {
        "status": "error",
        "error": "rlm.create_session is not available in this session",
    }

    reply = await dispatch_host_request(
        {"type": "rlm.create_session", "prompt": 5}, session_id=SID
    )
    assert reply["error"] == "rlm.create_session prompt must be a string"


# =================================================================================================
# refine.* / compact.*
# =================================================================================================


@pytest.mark.asyncio
async def test_refine_run_schedules_and_drains(dispatcher):
    status = await dispatch_host_request({"type": "refine.status"}, session_id=SID)
    assert status["result"] == {"pending": False, "in_flight": False}

    reply = await dispatch_host_request(
        {"type": "refine.run", "instructions": "keep tests", "global": True}, session_id=SID
    )
    assert reply == {"status": "ok", "result": {"scheduled": True, "note": REFINE_NOTE}}

    status = await dispatch_host_request({"type": "refine.status"}, session_id=SID)
    assert status["result"]["pending"] is True

    pending = drain_pending_refine(SID)
    assert pending == {"instructions": "keep tests", "global": True}
    assert drain_pending_refine(SID) is None

    status = await dispatch_host_request({"type": "refine.status"}, session_id=SID)
    assert status["result"]["pending"] is False


@pytest.mark.asyncio
async def test_refine_in_flight_flag_is_settable(dispatcher):
    reg.set_refine_in_flight(SID, True)
    status = await dispatch_host_request({"type": "refine.status"}, session_id=SID)
    assert status["result"]["in_flight"] is True
    reg.set_refine_in_flight(SID, False)
    status = await dispatch_host_request({"type": "refine.status"}, session_id=SID)
    assert status["result"]["in_flight"] is False


@pytest.mark.asyncio
async def test_refine_without_active_turn_reports_prime_reason():
    reply = await dispatch_host_request({"type": "refine.run"}, session_id=None)
    assert reply == {
        "status": "ok",
        "result": {
            "scheduled": False,
            "reason": "no active turn; refine can only be requested while a turn is running",
        },
    }


@pytest.mark.asyncio
async def test_refine_validation_strings():
    reply = await dispatch_host_request({"type": "refine.run", "instructions": 5}, session_id=SID)
    assert reply["error"] == "refine.run instructions must be a string when provided"

    reply = await dispatch_host_request({"type": "refine.run", "global": "yes"}, session_id=SID)
    assert reply["error"] == "refine.run global must be a boolean when provided"


@pytest.mark.asyncio
async def test_compact_run_schedules_and_drains(dispatcher):
    reply = await dispatch_host_request(
        {"type": "compact.run", "instructions": "keep the failures"}, session_id=SID
    )
    assert reply == {"status": "ok", "result": {"scheduled": True, "note": COMPACT_NOTE}}

    pending = drain_pending_compact(SID)
    assert pending == {"instructions": "keep the failures"}
    assert drain_pending_compact(SID) is None


@pytest.mark.asyncio
async def test_compact_without_active_turn_reports_prime_reason():
    reply = await dispatch_host_request({"type": "compact.run"}, session_id=None)
    assert reply == {
        "status": "ok",
        "result": {
            "scheduled": False,
            "reason": "no active turn; compaction can only be requested while a turn is running",
        },
    }


@pytest.mark.asyncio
async def test_compact_status_shape(dispatcher):
    reply = await dispatch_host_request({"type": "compact.status"}, session_id=SID)
    assert reply["status"] == "ok"
    assert set(reply["result"]) == {"tokens", "context_window", "percent", "scheduled"}
    assert reply["result"]["scheduled"] is False


@pytest.mark.asyncio
async def test_compact_validation_string():
    reply = await dispatch_host_request({"type": "compact.run", "instructions": 5}, session_id=SID)
    assert reply["error"] == "compact.run instructions must be a string when provided"


# =================================================================================================
# agent_message.*
# =================================================================================================


@pytest.mark.asyncio
async def test_agent_message_list_agents_removed_string():
    reply = await dispatch_host_request({"type": "agent_message.list_agents"}, session_id=SID)
    assert reply == {"status": "error", "error": LIST_AGENTS_REMOVED}


@pytest.mark.asyncio
async def test_agent_message_to_parent_queues_notice(dispatcher):
    reply = await dispatch_host_request(
        {"type": "agent_message.send", "message": "hello parent", "receiver_role": "parent"},
        session_id=SID,
    )
    receipt = reply["result"]
    assert receipt["source"] == "agent_message"
    assert receipt["target"] == "parent"
    assert receipt["message"] == "hello parent"
    assert receipt["deliveryStatus"] == "queued"
    assert receipt["deliveryMode"] == "steer"
    assert isinstance(receipt["queuedAt"], str) and receipt["queuedAt"].endswith("Z")
    assert receipt["id"].startswith("agentmsg_")

    notices = drain_notices(SID)
    assert notices == [f"[agent-message from child:{SID}]\n\nhello parent"]
    assert drain_notices(SID) == []


@pytest.mark.asyncio
async def test_agent_message_validation_strings():
    reply = await dispatch_host_request(
        {"type": "agent_message.send", "message": "hi", "receiver_role": "cousin"}, session_id=SID
    )
    assert (
        reply["error"]
        == 'agent_message.send receiver_role must be "parent", "sibling", or "child"'
    )

    reply = await dispatch_host_request(
        {
            "type": "agent_message.send",
            "message": "hi",
            "receiver_role": "parent",
            "receiver_name": "boss",
        },
        session_id=SID,
    )
    assert reply["error"] == "agent_message.send receiver_name must be omitted for parent messages"

    reply = await dispatch_host_request(
        {"type": "agent_message.send", "message": "hi", "receiver_role": "child"}, session_id=SID
    )
    assert (
        reply["error"]
        == "agent_message.send receiver_name is required for sibling and child messages"
    )

    reply = await dispatch_host_request(
        {"type": "agent_message.send", "message": 5, "receiver_role": "parent"}, session_id=SID
    )
    assert reply["error"] == "agent_message.send message must be a string"

    reply = await dispatch_host_request(
        {"type": "agent_message.send", "message": "   ", "receiver_role": "parent"}, session_id=SID
    )
    assert reply["error"] == "Agent session message cannot be empty"

    reply = await dispatch_host_request(
        {"type": "agent_message.send", "message": "x" * 16_385, "receiver_role": "parent"},
        session_id=SID,
    )
    assert reply["error"] == "Agent session message is too long: 16385 chars exceeds 16384"

    reply = await dispatch_host_request(
        {
            "type": "agent_message.send",
            "message": "hi",
            "receiver_role": "sibling",
            "receiver_name": "nobody",
        },
        session_id=SID,
    )
    assert reply["error"] == 'No sibling matches "nobody"'

    reply = await dispatch_host_request(
        {
            "type": "agent_message.send",
            "message": "hi",
            "receiver_role": "child",
            "receiver_name": "ghost",
        },
        session_id=SID,
    )
    assert reply["error"] == 'No child matches "ghost"'


@pytest.mark.asyncio
async def test_agent_message_child_without_inbox_reports_honestly(dispatcher, monkeypatch):
    install_fake_runner(monkeypatch)
    reply = await dispatch_host_request(
        {"type": "rlm.run", "prompt": "spawn me", "kwargs": {"name": "helper"}}, session_id=SID
    )
    assert reply["status"] == "ok"

    reply = await dispatch_host_request(
        {
            "type": "agent_message.send",
            "message": "hi child",
            "receiver_role": "child",
            "receiver_name": "helper",
        },
        session_id=SID,
    )
    assert reply["status"] == "error"
    assert "has no message inbox" in reply["error"]


@pytest.mark.asyncio
async def test_agent_message_broadcast_returns_receipts(dispatcher, monkeypatch):
    install_fake_runner(monkeypatch)
    spawn = await dispatch_host_request(
        {"type": "rlm.run", "prompt": "spawn me", "kwargs": {"name": "helper"}}, session_id=SID
    )
    child_id = spawn["result"]["rlm_child_id"]

    reply = await dispatch_host_request(
        {"type": "agent_message.send", "message": "all hands", "target": "all"}, session_id=SID
    )
    receipts = reply["result"]["receipts"]
    assert receipts[0]["deliveryStatus"] == "queued"
    assert receipts[0]["target"] == "parent"
    child_receipt = next(r for r in receipts if r.get("target") == child_id)
    assert "no message inbox" in child_receipt["error"]

    reply = await dispatch_host_request(
        {
            "type": "agent_message.send",
            "message": "all hands",
            "target": "all",
            "receiver_role": "parent",
        },
        session_id=SID,
    )
    assert (
        reply["error"]
        == "agent_message.send broadcast cannot be combined with receiver_role/receiver_name"
    )

    reply = await dispatch_host_request(
        {"type": "agent_message.send", "message": "hi", "target": "someone"}, session_id=SID
    )
    assert reply["error"].startswith("positional agent_message.send targets are not supported; ")


# =================================================================================================
# agent_observe.*
# =================================================================================================


@pytest.mark.asyncio
async def test_observe_list_includes_current_and_children(dispatcher, monkeypatch):
    install_fake_runner(monkeypatch, final_text="child answer")
    spawn = await dispatch_host_request(
        {"type": "rlm.run", "prompt": "spawn me", "kwargs": {"name": "helper"}}, session_id=SID
    )
    await settle(dispatcher, spawn_record(spawn))

    reply = await dispatch_host_request({"type": "agent_observe.list"}, session_id=SID)
    current = reply["result"]["current"]
    agents = reply["result"]["agents"]
    assert current["sessionId"] == SID
    assert current["isCurrent"] is True
    assert len(agents) == 2
    child = next(a for a in agents if not a["isCurrent"])
    assert child["sessionName"] == "helper"
    assert child["relationship"] == "child"
    assert child["runtimeKind"] == "subagent"
    assert child["rlmChildId"] == spawn["result"]["rlm_child_id"]


@pytest.mark.asyncio
async def test_observe_get_target_validation_and_lookup(dispatcher):
    reply = await dispatch_host_request({"type": "agent_observe.get", "target": 7}, session_id=SID)
    assert reply["error"] == "agent_observe.get target must be a string"

    reply = await dispatch_host_request(
        {"type": "agent_observe.get", "target": "ghost"}, session_id=SID
    )
    assert reply["error"] == 'No child matches "ghost"'

    reply = await dispatch_host_request(
        {"type": "agent_observe.get", "target": "current"}, session_id=SID
    )
    assert reply["result"]["agent"]["isCurrent"] is True


@pytest.mark.asyncio
async def test_observe_recent_clamp_strings(dispatcher):
    reply = await dispatch_host_request(
        {"type": "agent_observe.recent", "target": "current", "limit": 0}, session_id=SID
    )
    assert reply["error"] == "agent_observe limit must be between 1 and 50"

    reply = await dispatch_host_request(
        {"type": "agent_observe.recent", "target": "current", "limit": 51}, session_id=SID
    )
    assert reply["error"] == "agent_observe limit must be between 1 and 50"

    reply = await dispatch_host_request(
        {"type": "agent_observe.recent", "target": "current", "limit": "8"}, session_id=SID
    )
    assert reply["error"] == "agent_observe limit must be an integer"

    reply = await dispatch_host_request(
        {"type": "agent_observe.recent", "target": "current", "max_chars": 79}, session_id=SID
    )
    assert reply["error"] == "agent_observe max_chars must be between 80 and 2000"

    reply = await dispatch_host_request(
        {"type": "agent_observe.recent", "target": "current", "max_chars": 2_001}, session_id=SID
    )
    assert reply["error"] == "agent_observe max_chars must be between 80 and 2000"

    reply = await dispatch_host_request(
        {"type": "agent_observe.recent", "target": 5}, session_id=SID
    )
    assert reply["error"] == "agent_observe.recent target must be a string"


@pytest.mark.asyncio
async def test_observe_recent_child_messages_and_suffix_lookup(dispatcher, monkeypatch):
    install_fake_runner(monkeypatch, final_text="child answer " + "y" * 400)
    spawn = await dispatch_host_request(
        {"type": "rlm.run", "prompt": "do the thing", "kwargs": {"name": "helper"}}, session_id=SID
    )
    child_id = spawn["result"]["rlm_child_id"]
    await settle(dispatcher, spawn_record(spawn))

    reply = await dispatch_host_request(
        {"type": "agent_observe.recent", "target": child_id[-6:], "limit": 1, "max_chars": 80},
        session_id=SID,
    )
    assert reply["status"] == "ok"
    result = reply["result"]
    assert result["limit"] == 1
    assert result["maxChars"] == 80
    assert len(result["messages"]) == 1
    message = result["messages"][0]
    assert message["role"] == "assistant"
    assert message["truncated"] is True
    assert len(message["text"]) == 80


@pytest.mark.asyncio
async def test_observe_recent_current_defaults(dispatcher):
    reply = await dispatch_host_request(
        {"type": "agent_observe.recent", "target": "current"}, session_id=SID
    )
    result = reply["result"]
    assert result["limit"] == 8
    assert result["maxChars"] == 800
    assert result["messages"] == []
    assert result["truncated"] is False


# =================================================================================================
# goal.*
# =================================================================================================


class FakeGoalService:
    def __init__(self, *, disabled=False, focused=None):
        self._disabled = disabled
        self._focused = focused
        self.created_kwargs = None
        self.completed_id = None

    @property
    def settings(self) -> dict:
        return {"disabled": self._disabled}

    def focused(self):
        return self._focused

    def create(self, objective, *, token_budget=None, source="create_goal", **kwargs):
        self.created_kwargs = {
            "objective": objective,
            "token_budget": token_budget,
            "source": source,
        }
        record = SimpleNamespace(
            id="goal_1",
            objective=objective,
            status="active",
            token_budget=token_budget,
            tokens_used=0,
            time_used_seconds=0,
            created_at=1,
            updated_at=1,
        )
        self._focused = record
        return record

    def set_status(self, goal_id, status, **kwargs):
        self.completed_id = goal_id
        self._focused.status = status
        return self._focused


@pytest.fixture
def goal_service(monkeypatch):
    service = FakeGoalService()

    def fake_get_service(cwd):
        return service

    monkeypatch.setattr("vtx.ai.agent.goal.service.get_service", fake_get_service)
    return service


@pytest.mark.asyncio
async def test_goals_disabled_gate_uses_exact_message(monkeypatch, dispatcher):
    service = FakeGoalService(disabled=True)
    monkeypatch.setattr("vtx.ai.agent.goal.service.get_service", lambda cwd, s=service: s)
    reply = await dispatch_host_request({"type": "goal.get"}, session_id=SID)
    assert reply == {"status": "error", "error": "goals are disabled in this session"}

    reply = await dispatch_host_request(
        {"type": "goal.create", "objective": "ship it"}, session_id=SID
    )
    assert reply["error"] == "goals are disabled in this session"


@pytest.mark.asyncio
async def test_goal_get_without_goal_returns_null_shape(dispatcher, goal_service):
    reply = await dispatch_host_request({"type": "goal.get"}, session_id=SID)
    assert reply["result"] == {
        "goal": None,
        "remaining_tokens": None,
        "completion_budget_report": None,
    }


@pytest.mark.asyncio
async def test_goal_create_validation_and_shape(dispatcher, goal_service):
    reply = await dispatch_host_request({"type": "goal.create", "objective": 5}, session_id=SID)
    assert reply["error"] == "goal.create objective must be a string"

    reply = await dispatch_host_request(
        {"type": "goal.create", "objective": "ship it", "token_budget": "100"}, session_id=SID
    )
    assert reply["error"] == "goal.create token_budget must be an integer when provided"

    reply = await dispatch_host_request(
        {"type": "goal.create", "objective": "ship it", "token_budget": 1000}, session_id=SID
    )
    assert reply["status"] == "ok"
    goal = reply["result"]["goal"]
    assert goal["objective"] == "ship it"
    assert goal["status"] == "active"
    assert goal["token_budget"] == 1000
    assert reply["result"]["remaining_tokens"] == 1000
    assert goal_service.created_kwargs["token_budget"] == 1000

    # An open goal blocks creating another one.
    reply = await dispatch_host_request(
        {"type": "goal.create", "objective": "another"}, session_id=SID
    )
    assert reply["error"].startswith(
        "cannot create a new goal because this thread already has an active goal;"
    )


@pytest.mark.asyncio
async def test_goal_complete_round_trip(dispatcher, goal_service):
    reply = await dispatch_host_request({"type": "goal.complete"}, session_id=SID)
    assert reply["error"] == "cannot complete goal because this thread has no goal"

    await dispatch_host_request({"type": "goal.create", "objective": "ship it"}, session_id=SID)
    reply = await dispatch_host_request({"type": "goal.complete"}, session_id=SID)
    assert reply["status"] == "ok"
    assert reply["result"]["goal"]["status"] == "complete"
    assert goal_service.completed_id == "goal_1"


@pytest.mark.asyncio
async def test_goal_complete_without_goal_reports_prime_string(dispatcher, goal_service):
    reply = await dispatch_host_request({"type": "goal.complete"}, session_id=SID)
    assert reply["error"] == "cannot complete goal because this thread has no goal"

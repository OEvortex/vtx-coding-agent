"""Tests that the local continual harness is branch-correct.

The local harness used to be one JSON file per session, so a revert or a branch
still showed the memories and skills written on the abandoned path: the file is
not in the session tree, so nothing rewinds it. These tests pin the replacement
behaviour — local state replays from the branch, so it obeys the same semantics
as messages.
"""

from __future__ import annotations

import json
from importlib import import_module
from pathlib import Path

import pytest

from vtx.ai.agent.revert import revert_to
from vtx.ai.agent.rlm.harness import HarnessState
from vtx.ai.agent.session import Session

#: ``rlm/__init__.py`` binds a ``harness`` *object* that shadows the submodule
#: attribute, so the module has to be imported by path to reach its cache.
harness_module = import_module("vtx.ai.agent.rlm.harness")


def _session(tmp_path: Path) -> Session:
    return Session(session_id="test-session", cwd=str(tmp_path), persist=False)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A git worktree, because ``revert_to`` snapshots the tree as it rewinds.

    Using the real revert path rather than poking the leaf id keeps these tests
    honest: the point is that a user-visible ``/undo`` rewinds the harness, and
    that is the code that has to do it.
    """
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "file.txt").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@e", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=tmp_path,
        check=True,
    )
    return tmp_path


def _bindings(session: Session):
    """The reader/writer pair the host derives from a live session."""
    return (
        lambda: session.replay_harness_state(),
        lambda added, removed, refinements: session.append_harness_state(
            added, removed, refinements
        ),
    )


def _state(session: Session) -> HarnessState:
    reader, writer = _bindings(session)
    return HarnessState(in_memory=True, scope="local", branch_reader=reader, branch_writer=writer)


# --- session-level replay ---------------------------------------------------


def test_a_write_is_visible_on_the_branch(tmp_path: Path):
    session = _session(tmp_path)
    state = _state(session)
    state.upsert("memory", "Deploy runbook", "Run make deploy first.")
    assert [entry.id for entry in state.list("memory")] == ["deploy_runbook"]


def test_replay_returns_the_written_entry(tmp_path: Path):
    session = _session(tmp_path)
    _state(session).upsert("memory", "Note", "content")
    entries, _refinements = session.replay_harness_state()
    assert "memory:note" in entries


def test_a_revert_undoes_a_harness_write(repo: Path):
    """The behaviour that motivated moving the harness into the tree.

    The write happens, then the session reverts past it. A file-backed store
    would still show the memory, because nothing rewinds the file.
    """
    session = _session(repo)
    boundary = session.append_message(_user("start"))
    state = _state(session)
    state.upsert("memory", "Should vanish", "written after the user turn")
    assert state.list("memory")

    revert_to(session, boundary, cwd=str(repo), commit_now=True)

    reread = _state(session)
    assert reread.list("memory") == []


def test_a_branch_does_not_see_the_other_branchs_write(repo: Path):
    """Two leaves off one fork each see only their own writes.

    Asserted through ``get_branch(leaf_id)`` — the primitive both ``/tree`` and
    ``revert_to`` navigate by — rather than by moving the live leaf, because
    ``unrevert`` only stages the worktree reversal and does not advance the leaf.
    """
    session = _session(repo)
    fork = session.append_message(_user("fork point"))

    main_state = _state(session)
    main_state.upsert("memory", "Main only", "on the main path")
    main_leaf = session.leaf_id

    # A sibling branch off the same fork. ``LeafEntry`` is how the tree records
    # a new active point, which is what makes the writes above and below land on
    # different paths.
    from vtx.ai.agent.session import LeafEntry

    session._append_entry(
        LeafEntry(id="branch-leaf", parent_id=main_leaf, timestamp="", target_id=fork)
    )
    branch_state = _state(session)
    assert branch_state.list("memory") == []
    branch_state.upsert("memory", "Branch only", "on the branch path")
    # The leaf is now the branch's own harness entry, since a LeafEntry moves
    # the active point to its target and the write appended from there.
    branch_leaf = session.leaf_id
    assert branch_leaf is not None and branch_leaf != main_leaf

    # Neither path sees the other's write.
    main_entries, _ = session.replay_harness_state(main_leaf)
    assert set(main_entries) == {"memory:main_only"}
    branch_entries, _ = session.replay_harness_state(branch_leaf)
    assert set(branch_entries) == {"memory:branch_only"}


def test_later_writes_win(tmp_path: Path):
    session = _session(tmp_path)
    state = _state(session)
    state.upsert("memory", "Note", "first")
    state.upsert("memory", "Note", "second")
    entries, _ = session.replay_harness_state()
    assert entries["memory:note"]["content"] == "second"


def test_a_delete_is_replayed(tmp_path: Path):
    session = _session(tmp_path)
    state = _state(session)
    state.upsert("memory", "Note", "content")
    state.delete("memory", "note")
    entries, _ = session.replay_harness_state()
    assert "memory:note" not in entries


def test_replay_ignores_a_key_in_both_set_and_delete(tmp_path: Path):
    """A key in both lists of one entry is dropped, so a write is atomic."""
    session = _session(tmp_path)
    session.append_harness_state(
        {"memory:x": {"id": "x", "kind": "memory", "title": "X", "content": "c"}}, ["memory:x"]
    )
    entries, _ = session.replay_harness_state()
    assert "memory:x" not in entries


def test_replay_of_an_empty_session_is_empty(tmp_path: Path):
    entries, refinements = _session(tmp_path).replay_harness_state()
    assert entries == {}
    assert refinements == []


# --- delta behaviour --------------------------------------------------------


def test_only_the_delta_is_committed(tmp_path: Path):
    """The branch must not grow by the whole store on every write."""
    session = _session(tmp_path)
    reader, writer = _bindings(session)
    state = HarnessState(in_memory=True, scope="local", branch_reader=reader, branch_writer=writer)
    state.upsert("memory", "One", "a")
    state.upsert("memory", "Two", "b")

    harness_entries = [e for e in session.all_entries if e.type == "harness_state"]
    assert len(harness_entries) == 2
    # The second write ships one entry, not the whole store.
    assert set(harness_entries[1].set) == {"memory:two"}
    assert harness_entries[1].delete == []


def test_a_no_op_save_appends_nothing(tmp_path: Path):
    session = _session(tmp_path)
    reader, writer = _bindings(session)
    state = HarnessState(in_memory=True, scope="local", branch_reader=reader, branch_writer=writer)
    state.upsert("memory", "One", "a")
    before = len([e for e in session.all_entries if e.type == "harness_state"])
    state.save()
    after = len([e for e in session.all_entries if e.type == "harness_state"])
    assert after == before


def test_a_delete_commits_a_delete_not_a_snapshot(tmp_path: Path):
    session = _session(tmp_path)
    reader, writer = _bindings(session)
    state = HarnessState(in_memory=True, scope="local", branch_reader=reader, branch_writer=writer)
    state.upsert("memory", "One", "a")
    state.upsert("memory", "Two", "b")
    state.delete("memory", "one")

    last = [e for e in session.all_entries if e.type == "harness_state"][-1]
    assert last.delete == ["memory:one"]
    assert last.set == {}


# --- state-object behaviour -------------------------------------------------


def test_branch_backed_state_reports_itself(tmp_path: Path):
    assert _state(_session(tmp_path)).branch_backed is True


def test_file_backed_state_is_not_branch_backed(tmp_path: Path):
    state = HarnessState(tmp_path / "harness.json", scope="local")
    assert state.branch_backed is False


def test_the_global_store_stays_file_backed(tmp_path: Path):
    """The global store is cross-session, so it has no branch to belong to."""
    reader, writer = _bindings(_session(tmp_path))
    state = HarnessState(
        tmp_path / "global.json", scope="global", branch_reader=reader, branch_writer=writer
    )
    assert state.branch_backed is False


def test_a_broken_reader_degrades_to_empty(tmp_path: Path):
    """The harness must never be the reason a kernel cell fails."""

    def boom() -> tuple[dict, list]:
        raise RuntimeError("session gone")

    state = HarnessState(in_memory=True, scope="local", branch_reader=boom)
    assert state.list("memory") == []


def test_a_malformed_replayed_entry_is_dropped(tmp_path: Path):
    """Branch data is as untrusted as file data: same validation, same result."""
    session = _session(tmp_path)
    session.append_harness_state(
        {
            "memory:good": {"id": "good", "kind": "memory", "title": "G", "content": "c"},
            "memory:bad": {"id": "bad", "kind": "memory"},  # no title/content
        },
        [],
    )
    state = _state(session)
    assert [entry.id for entry in state.list("memory")] == ["good"]


def test_a_malformed_kind_prefix_is_ignored(tmp_path: Path):
    session = _session(tmp_path)
    session.append_harness_state(
        {
            "bogus:x": {"id": "x", "title": "T", "content": "c"},
            "memory:y": {"id": "y", "title": "T", "content": "c"},
        },
        [],
    )
    assert [entry.id for entry in _state(session).list("memory")] == ["y"]


def test_entries_persist_through_the_session_file(tmp_path: Path):
    """A branch entry is an ordinary session entry, so resume keeps it."""
    path = tmp_path / "session.jsonl"
    session = Session(
        session_id="test-session", cwd=str(tmp_path), session_file=path, persist=True
    )
    # An assistant turn is required before the session persists incrementally.
    session.append_message(_user("please remember"))
    session.append_message(_assistant("noted"))
    _state(session).upsert("memory", "Durable", "survives a restart")

    lines = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert any(line.get("type") == "harness_state" for line in lines)


def test_a_harness_entry_round_trips_through_the_wire_format(tmp_path: Path):
    """The entry must deserialize back into the same shape it was written in.

    A branch entry that only reads back in the live process would silently lose
    every memory on resume, so the serialization is asserted directly rather than
    through a whole session reload.
    """
    from vtx.ai.agent.session import HarnessStateEntry

    session = _session(tmp_path)
    _state(session).upsert("memory", "Durable", "survives a restart")
    written = [e for e in session.all_entries if e.type == "harness_state"][-1]

    restored = HarnessStateEntry.model_validate_json(written.model_dump_json())
    assert set(restored.set) == {"memory:durable"}
    assert restored.set["memory:durable"]["content"] == "survives a restart"
    assert restored.parent_id == written.parent_id


def test_harness_entries_are_not_sent_to_the_provider(tmp_path: Path):
    """Audit-only, like every other non-message entry."""
    session = _session(tmp_path)
    _state(session).upsert("memory", "Note", "c")
    assert session.messages == []


# --- default wiring ---------------------------------------------------------


def test_get_harness_state_adopts_the_live_session(tmp_path: Path, monkeypatch):
    """Every host-side caller inherits branch semantics from one place."""
    from vtx.ai.agent import dispatcher

    session = _session(tmp_path)
    monkeypatch.setattr(dispatcher, "get_context", lambda: _Ctx(session))
    monkeypatch.setattr("vtx.ai.agent.rlm.host._live_session", lambda: session)
    harness_module._state_cache.clear()

    state = harness_module.get_harness_state(tmp_path / "unused.json")
    assert state.branch_backed is True
    harness_module._state_cache.clear()


def test_get_harness_state_falls_back_to_file_without_a_session(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("vtx.ai.agent.rlm.host._live_session", lambda: None)
    harness_module._state_cache.clear()

    state = harness_module.get_harness_state(tmp_path / "fallback.json")
    assert state.branch_backed is False
    harness_module._state_cache.clear()


def test_two_sessions_do_not_share_a_store(tmp_path: Path):
    """The cache key includes the bindings' identity."""
    harness_module._state_cache.clear()
    one = _state(_session(tmp_path))
    two = _state(_session(tmp_path))
    assert one is not two
    harness_module._state_cache.clear()


class _Ctx:
    def __init__(self, session: Session) -> None:
        self.session = session


def _user(text: str):
    from vtx.core.types import UserMessage

    return UserMessage(content=text)


def _assistant(text: str):
    from vtx.core.types import AssistantMessage, TextContent

    return AssistantMessage(content=[TextContent(type="text", text=text)])

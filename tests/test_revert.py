"""Tests for working-tree snapshots and turn-level undo/redo.

These cover the two failure modes that matter most: a revert that silently
corrupts the worktree, and a revert that cannot be undone again.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from vtx.ai.agent import revert as rv
from vtx.ai.agent.session import Session
from vtx.ai.agent.snapshot import SnapshotStore, get_store
from vtx.core.types import FileChanges, TextContent, ToolResultMessage, UserMessage


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture()
def project(tmp_path: Path, monkeypatch) -> Path:
    """A disposable git project with the snapshot store redirected into tmp."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    proj = tmp_path / "proj"
    proj.mkdir()
    _git(proj, "init", "-q", ".")
    (proj / ".gitignore").write_text("node_modules/\n*.log\n")
    (proj / "a.txt").write_text("a0\n")
    (proj / "keep.txt").write_text("keep\n")
    _git(proj, "add", "-A")
    _git(proj, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
    return proj


@pytest.fixture()
def store(project: Path) -> SnapshotStore:
    return get_store(str(project))


def _session(project: Path) -> Session:
    return Session(cwd=str(project), session_id="test-session")


def _ask(session: Session, store: SnapshotStore, text: str) -> str:
    """A user message plus the snapshot marking the start of its turn."""
    entry = session.append_message(UserMessage(content=text))
    rv.record_turn_snapshot(session, store.capture())
    return entry


def _agent_edit(session: Session, path: str, added: int = 1, removed: int = 1) -> None:
    session.append_message(
        ToolResultMessage(
            tool_name="edit",
            tool_call_id=f"call-{len(session.all_entries)}",
            content=[TextContent(text="ok")],
            file_changes=FileChanges(path=path, added=added, removed=removed),
        )
    )


# ---------------------------------------------------------------------------
# snapshots
# ---------------------------------------------------------------------------


class TestSnapshotStore:
    def test_capture_is_content_addressed(self, project: Path, store: SnapshotStore) -> None:
        first = store.capture()
        assert first
        # Same content, same id: capturing twice costs nothing.
        assert store.capture() == first

        (project / "a.txt").write_text("changed\n")
        second = store.capture()
        assert second and second != first

    def test_gitignored_paths_are_never_hashed(self, project: Path, store: SnapshotStore) -> None:
        before = store.capture()
        (project / "node_modules").mkdir()
        (project / "node_modules" / "junk.js").write_text("x" * 5000)
        (project / "debug.log").write_text("noise")
        after = store.capture()
        assert store.files(before or "", after or "") == []

    def test_diff_reports_status_and_line_counts(
        self, project: Path, store: SnapshotStore
    ) -> None:
        t0 = store.capture()
        (project / "a.txt").write_text("a1\n")
        (project / "new.txt").write_text("n\n")
        (project / "keep.txt").unlink()
        t1 = store.capture()

        diffs = {d.path: d for d in store.diff(t0 or "", t1 or "")}
        assert set(diffs) == {"a.txt", "new.txt", "keep.txt"}
        assert diffs["new.txt"].status == "added"
        assert diffs["keep.txt"].status == "deleted"
        assert diffs["a.txt"].status == "modified"
        assert diffs["a.txt"].additions == 1
        assert diffs["a.txt"].deletions == 1

    def test_restore_round_trips_content_and_mode(
        self, project: Path, store: SnapshotStore
    ) -> None:
        script = project / "run.sh"
        script.write_text("#!/bin/sh\necho hi\n")
        script.chmod(0o755)
        blob = project / "bin.dat"
        blob.write_bytes(b"\x00\x01\xffbinary")
        t0 = store.capture()

        script.chmod(0o644)
        (project / "a.txt").write_text("a1\n")
        blob.write_bytes(b"\x00\x02\xfechanged")
        (project / "new.txt").write_text("n\n")
        t1 = store.capture()

        report = store.restore({p: t0 or "" for p in store.files(t0 or "", t1 or "")})
        assert not report.failed, report.failed
        assert (project / "a.txt").read_text() == "a0\n"
        assert blob.read_bytes() == b"\x00\x01\xffbinary"
        assert script.stat().st_mode & 0o777 == 0o755
        assert not (project / "new.txt").exists(), "absent from the target tree must be removed"

    def test_restore_is_selective(self, project: Path, store: SnapshotStore) -> None:
        t0 = store.capture()
        (project / "a.txt").write_text("a1\n")
        (project / "keep.txt").write_text("also changed\n")
        store.restore({"a.txt": t0 or ""})
        assert (project / "a.txt").read_text() == "a0\n"
        assert (project / "keep.txt").read_text() == "also changed\n"

    def test_restore_refuses_path_traversal(self, project: Path, store: SnapshotStore) -> None:
        t0 = store.capture()
        report = store.restore({"../escape.txt": t0 or "", "/etc/passwd": t0 or ""})
        assert report.failed == ["../escape.txt", "/etc/passwd"]
        assert not (project.parent / "escape.txt").exists()

    def test_non_git_directory_degrades_quietly(self, tmp_path: Path) -> None:
        plain = tmp_path / "plain"
        plain.mkdir()
        (plain / "x.txt").write_text("x")
        store = SnapshotStore(str(plain))
        assert store.available() is False
        assert store.capture() is None

    def test_store_is_cached_per_directory(self, project: Path) -> None:
        assert get_store(str(project)) is get_store(str(project))


# ---------------------------------------------------------------------------
# undo / redo
# ---------------------------------------------------------------------------


class TestUndoRedo:
    def _three_turns(self, project: Path, store: SnapshotStore):
        """Three turns: a.txt a0 -> a1 (+b.txt) -> a2 (-b.txt) -> a3."""
        session = _session(project)
        u0 = _ask(session, store, "first ask")
        (project / "a.txt").write_text("a1\n")
        (project / "b.txt").write_text("b1\n")
        _agent_edit(session, "a.txt")
        _agent_edit(session, "b.txt", added=1, removed=0)

        u1 = _ask(session, store, "second ask")
        (project / "a.txt").write_text("a2\n")
        (project / "b.txt").unlink()
        _agent_edit(session, "a.txt")
        _agent_edit(session, "b.txt", added=0, removed=1)

        u2 = _ask(session, store, "third ask")
        (project / "a.txt").write_text("a3\n")
        _agent_edit(session, "a.txt")
        return session, u0, u1, u2

    def test_undo_walks_back_turn_by_turn(self, project: Path, store: SnapshotStore) -> None:
        session, u0, u1, u2 = self._three_turns(project, store)
        assert (project / "a.txt").read_text() == "a3\n"

        assert rv.previous_boundary(session) == u2
        rv.stage(session, u2, store=store, cwd=str(project))
        assert (project / "a.txt").read_text() == "a2\n"
        assert not (project / "b.txt").exists()

        assert rv.previous_boundary(session) == u1
        rv.stage(session, u1, store=store, cwd=str(project))
        assert (project / "a.txt").read_text() == "a1\n"
        assert (project / "b.txt").exists()

        assert rv.previous_boundary(session) == u0
        rv.stage(session, u0, store=store, cwd=str(project))
        assert (project / "a.txt").read_text() == "a0\n"
        assert not (project / "b.txt").exists()

        assert rv.previous_boundary(session) is None

    def test_redo_walks_forward(self, project: Path, store: SnapshotStore) -> None:
        session, u0, u1, u2 = self._three_turns(project, store)
        for boundary in (u2, u1, u0):
            rv.stage(session, boundary, store=store, cwd=str(project))

        assert rv.next_boundary(session) == u1
        rv.stage(session, u1, store=store, cwd=str(project))
        assert (project / "a.txt").read_text() == "a1\n"

        assert rv.next_boundary(session) == u2
        rv.stage(session, u2, store=store, cwd=str(project))
        assert (project / "a.txt").read_text() == "a2\n"

    def test_unrevert_at_the_tip_restores_the_original(
        self, project: Path, store: SnapshotStore
    ) -> None:
        session, _u0, _u1, u2 = self._three_turns(project, store)
        rv.stage(session, u2, store=store, cwd=str(project))
        assert rv.next_boundary(session) is None

        changed = rv.unrevert(session, store=store, cwd=str(project))
        assert changed >= 1
        assert rv.current_state(session) is None
        assert (project / "a.txt").read_text() == "a3\n"

    def test_staging_twice_leaves_one_live_state(
        self, project: Path, store: SnapshotStore
    ) -> None:
        """Regression: repeated undos used to strand earlier revert entries."""
        session, _u0, u1, u2 = self._three_turns(project, store)
        rv.stage(session, u2, store=store, cwd=str(project))
        rv.stage(session, u1, store=store, cwd=str(project))

        live = [
            e
            for e in session.get_branch()
            if e.type == "custom_message" and e.custom_type == rv.REVERT_ENTRY
        ]
        assert len(live) == 1, "only the newest revert may stay on the branch"

        rv.unrevert(session, store=store, cwd=str(project))
        assert rv.current_state(session) is None

    def test_revert_never_touches_files_the_agent_did_not_report(
        self, project: Path, store: SnapshotStore
    ) -> None:
        session = _session(project)
        boundary = _ask(session, store, "do the thing")
        (project / "a.txt").write_text("agent change\n")
        _agent_edit(session, "a.txt")
        # The user edits a different file by hand; the agent never claimed it.
        (project / "keep.txt").write_text("USER EDIT\n")

        state = rv.stage(session, boundary, store=store, cwd=str(project))
        assert [d.path for d in state.files] == ["a.txt"]
        assert (project / "keep.txt").read_text() == "USER EDIT\n"

    def test_commit_is_non_destructive(self, project: Path, store: SnapshotStore) -> None:
        session, _u0, _u1, u2 = self._three_turns(project, store)
        _ask(session, store, "fourth ask")
        before = len(session.get_branch())
        total_before = len(session.all_entries)

        rv.stage(session, u2, store=store, cwd=str(project))
        committed = rv.commit(session)
        branch = session.get_branch()

        assert committed == u2
        assert len(branch) < before, "the reverted turns leave the active branch"
        assert u2 in [e.id for e in branch], "the boundary itself stays"
        assert rv.current_state(session) is None
        assert len(session.all_entries) >= total_before, "old entries survive for redo"

    def test_redo_after_commit_can_reach_the_kept_branch(
        self, project: Path, store: SnapshotStore
    ) -> None:
        session, _u0, u1, u2 = self._three_turns(project, store)
        rv.stage(session, u2, store=store, cwd=str(project))
        rv.commit(session)
        # The dropped turn is still addressable, which is the point of the
        # non-destructive rewind.
        assert session.get_branch(u2)
        assert [e.id for e in session.get_branch(u2)][-1] == u2
        assert u1 in [e.id for e in session.get_branch(u1)]

    def test_boundary_without_a_snapshot_is_refused(
        self, project: Path, store: SnapshotStore
    ) -> None:
        session = _session(project)
        legacy = session.append_message(UserMessage(content="recorded before snapshots"))
        with pytest.raises(rv.RevertError, match="no snapshot recorded"):
            rv.stage(session, legacy, store=store, cwd=str(project))

    def test_unknown_boundary_is_refused(self, project: Path, store: SnapshotStore) -> None:
        session = _session(project)
        _ask(session, store, "hello")
        with pytest.raises(rv.RevertError):
            rv.stage(session, "not-a-real-entry", store=store, cwd=str(project))

    def test_describe_is_human_readable(self, project: Path, store: SnapshotStore) -> None:
        session, _u0, _u1, u2 = self._three_turns(project, store)
        state = rv.stage(session, u2, store=store, cwd=str(project))
        text = rv.describe(state)
        assert "third ask" in text
        assert "1 files" in text

    def test_state_survives_a_json_round_trip(self, project: Path, store: SnapshotStore) -> None:
        session, _u0, _u1, u2 = self._three_turns(project, store)
        state = rv.stage(session, u2, store=store, cwd=str(project))
        restored = rv.RevertState.from_json(state.to_json())
        assert restored is not None
        assert restored.boundary_id == state.boundary_id
        assert restored.boundary_label == state.boundary_label
        assert [d.path for d in restored.files] == [d.path for d in state.files]

    def test_corrupt_state_json_is_ignored(self) -> None:
        assert rv.RevertState.from_json("not json") is None
        assert rv.RevertState.from_json("{}") is None


class TestNavigation:
    def test_no_user_messages_means_nothing_to_undo(self, project: Path) -> None:
        session = _session(project)
        assert rv.previous_boundary(session) is None
        assert rv.next_boundary(session) is None

    def test_redo_without_a_revert_is_a_noop(self, project: Path, store: SnapshotStore) -> None:
        session = _session(project)
        _ask(session, store, "just one ask")
        assert rv.current_state(session) is None
        assert rv.next_boundary(session) is None

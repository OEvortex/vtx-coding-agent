"""Tests for working-tree snapshots and turn-level undo/redo.

Covers the failure modes that matter: a revert that silently restores nothing,
and a revert that cannot be undone again.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from vtx.agent import revert as rv
from vtx.agent.session import Session
from vtx.agent.snapshot import SnapshotStore, get_store
from vtx.protocol.types import FileChanges, TextContent, ToolResultMessage, UserMessage


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


def _turn(
    session: Session,
    store: SnapshotStore,
    project: Path,
    prompt: str,
    *,
    writes: dict[str, str | None] | None = None,
    report: bool = True,
) -> str:
    """Run one complete turn: capture start, prompt, mutate, close the bracket.

    Mirrors the runtime, which hashes the worktree before the turn and records
    ``start``/``files`` once the turn's tools have settled. ``writes`` maps a
    path to new content, or to ``None`` to delete it. ``report=False`` mutates
    without emitting a ``file_changes`` tool result, the way bash or ipython do.
    """
    start = store.capture()
    entry = session.append_message(UserMessage(content=prompt))
    for index, (path, content) in enumerate((writes or {}).items(), start=1):
        target = project / path
        if content is None:
            target.unlink()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        if report:
            session.append_message(
                ToolResultMessage(
                    tool_name="edit",
                    tool_call_id=f"call-{len(session.all_entries)}-{index}",
                    content=[TextContent(text="ok")],
                    file_changes=FileChanges(path=str(target), added=1, removed=1),
                )
            )
    rv.record_turn_snapshot(session, start, cwd=str(project))
    return entry


def _legacy_turn(session: Session, project: Path, prompt: str, path: str) -> str:
    """A turn recorded before snapshots existed (e.g. a resumed session)."""
    entry = session.append_message(UserMessage(content=prompt))
    (project / path).write_text("legacy change\n")
    session.append_message(
        ToolResultMessage(
            tool_name="edit",
            tool_call_id=f"legacy-{len(session.all_entries)}",
            content=[TextContent(text="ok")],
            file_changes=FileChanges(path=str(project / path), added=1, removed=1),
        )
    )
    return entry


# ---------------------------------------------------------------------------
# snapshots
# ---------------------------------------------------------------------------


class TestSnapshotStore:
    def test_capture_is_content_addressed(self, project: Path, store: SnapshotStore) -> None:
        first = store.capture()
        assert first
        assert store.capture() == first, "same content, same id"

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
        assert not (project / "new.txt").exists()

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

    def test_relative_normalizes_absolute_and_rejects_escapes(
        self, project: Path, store: SnapshotStore
    ) -> None:
        assert store.relative(str(project / "src" / "a.py")) == "src/a.py"
        assert store.relative("src/a.py") == "src/a.py"
        assert store.relative("../outside.py") is None
        assert store.relative("/etc/passwd") is None
        assert store.relative("") is None

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
# per-turn records
# ---------------------------------------------------------------------------


class TestTurnSnapshots:
    def test_record_captures_start_end_and_changed_files(
        self, project: Path, store: SnapshotStore
    ) -> None:
        session = _session(project)
        _turn(session, store, project, "edit a", writes={"a.txt": "a1\n"})

        records = rv._turn_snapshots(session)
        assert len(records) == 1
        record = records[0]
        assert record.start and record.end and record.start != record.end
        assert record.files == ["a.txt"]
        assert record.turn

    def test_a_turn_that_changed_nothing_records_no_files(
        self, project: Path, store: SnapshotStore
    ) -> None:
        session = _session(project)
        _turn(session, store, project, "just talk")
        assert rv._turn_snapshots(session)[0].files == []

    def test_files_include_edits_no_tool_reported(
        self, project: Path, store: SnapshotStore
    ) -> None:
        """The whole point of diffing the worktree instead of trusting tools.

        bash, python and ipython all mutate files without emitting a
        ``file_changes`` result, so an attribution-based list misses them.
        """
        session = _session(project)
        _turn(
            session,
            store,
            project,
            "shell out and edit",
            writes={"a.txt": "via bash\n", "uv.lock": "via bash\n"},
            report=False,
        )
        record = rv._turn_snapshots(session)[0]
        assert sorted(record.files) == ["a.txt", "uv.lock"]

    def test_no_snapshot_is_recorded_without_a_start_tree(
        self, project: Path, store: SnapshotStore
    ) -> None:
        session = _session(project)
        assert rv.record_turn_snapshot(session, None, cwd=str(project)) is None
        assert rv._turn_snapshots(session) == []

    def test_record_json_round_trips(self) -> None:
        record = rv.TurnSnapshot(turn="t1", start="aaa", end="bbb", files=["x.py"])
        again = rv.TurnSnapshot.from_json(record.to_json())
        assert again == record
        assert rv.TurnSnapshot.from_json("nope") is None
        assert rv.TurnSnapshot.from_json('{"start": "a"}') is None


# ---------------------------------------------------------------------------
# undo / redo
# ---------------------------------------------------------------------------


class TestUndoRedo:
    def _three_turns(self, project: Path, store: SnapshotStore):
        """a.txt a0 -> a1 (+b.txt) -> a2 (-b.txt) -> a3."""
        session = _session(project)
        u0 = _turn(session, store, project, "first ask", writes={"a.txt": "a1\n", "b.txt": "b1\n"})
        u1 = _turn(session, store, project, "second ask", writes={"a.txt": "a2\n", "b.txt": None})
        u2 = _turn(session, store, project, "third ask", writes={"a.txt": "a3\n"})
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
        session, _u0, u1, u2 = self._three_turns(project, store)
        for boundary in (u2, u1, _u0_boundary(session)):
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

        assert rv.unrevert(session, store=store, cwd=str(project)) >= 1
        assert rv.current_state(session) is None
        assert (project / "a.txt").read_text() == "a3\n"

    def test_staging_twice_leaves_one_live_state(
        self, project: Path, store: SnapshotStore
    ) -> None:
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

    def test_undo_restores_files_changed_without_tool_reports(
        self, project: Path, store: SnapshotStore
    ) -> None:
        """Regression: the reported failure mode from real use.

        A turn edited three files through ipython and bash. No tool reported
        them, so an attribution-based guard restored nothing and the UI said
        "no file changes" while the worktree stayed dirty.
        """
        session = _session(project)
        boundary = _turn(
            session,
            store,
            project,
            "bump the version",
            report=False,
            writes={"pyproject.toml": "1.2.1\n", "uv.lock": "locked\n", "version.py": "x = 1\n"},
        )
        assert rv.plan(session, boundary) != {}, "turn diff must be non-empty"

        state = rv.stage(session, boundary, store=store, cwd=str(project))
        assert sorted(d.path for d in state.files) == ["pyproject.toml", "uv.lock", "version.py"]
        # All three were created by that turn, so reverting removes them.
        for name in ("pyproject.toml", "uv.lock", "version.py"):
            assert not (project / name).exists(), f"{name} should have been removed"
        assert (project / "a.txt").read_text() == "a0\n", "untouched file survives"

    def test_commit_is_non_destructive(self, project: Path, store: SnapshotStore) -> None:
        session, _u0, _u1, u2 = self._three_turns(project, store)
        _turn(session, store, project, "fourth ask")
        before = len(session.get_branch())
        total_before = len(session.all_entries)

        rv.stage(session, u2, store=store, cwd=str(project))
        committed = rv.commit(session)
        branch = session.get_branch()

        assert committed == u2
        assert len(branch) < before
        assert u2 in [e.id for e in branch], "the boundary itself stays"
        assert rv.current_state(session) is None
        assert len(session.all_entries) >= total_before, "old entries survive for redo"

    def test_snapshot_found_after_a_commit_rewinds_the_leaf(
        self, project: Path, store: SnapshotStore
    ) -> None:
        """A committed revert restores the leaf, so the next turn's record
        lands at that restored point rather than after the boundary."""
        session, _u0, _u1, u2 = self._three_turns(project, store)
        rv.stage(session, u2, store=store, cwd=str(project))
        rv.commit(session)

        _turn(session, store, project, "after the commit", writes={"a.txt": "a4\n"})
        state = rv.stage(session, rv.previous_boundary(session), store=store, cwd=str(project))
        assert state.files_available is True
        assert (project / "a.txt").read_text() == "a2\n"

    def test_redo_after_commit_can_reach_the_kept_branch(
        self, project: Path, store: SnapshotStore
    ) -> None:
        session, _u0, u1, u2 = self._three_turns(project, store)
        rv.stage(session, u2, store=store, cwd=str(project))
        rv.commit(session)
        assert session.get_branch(u2)
        assert [e.id for e in session.get_branch(u2)][-1] == u2
        assert u1 in [e.id for e in session.get_branch(u1)]

    def test_legacy_turn_rewinds_conversation_only(
        self, project: Path, store: SnapshotStore
    ) -> None:
        """A resumed session has no record for pre-resume turns."""
        session = _session(project)
        legacy = _legacy_turn(session, project, "recorded before snapshots", "a.txt")
        (project / "a.txt").write_text("later change\n")

        state = rv.stage(session, legacy, store=store, cwd=str(project))
        assert state.files_available is False
        assert state.files == []
        assert "conversation only" in rv.describe(state)
        # The file is untouched: no record describes that point.
        assert (project / "a.txt").read_text() == "later change\n"
        assert rv.unrevert(session, store=store, cwd=str(project)) == 0
        assert rv.current_state(session) is None

    def test_boundary_for_entry_maps_any_entry_to_its_turn(
        self, project: Path, store: SnapshotStore
    ) -> None:
        """`/tree` can select a tool call or a record, not just a prompt.

        Undo units are turns, so any entry resolves to the user message that
        started its turn; that is the boundary the restore plan understands.
        """
        session = _session(project)
        u1 = _turn(session, store, project, "first ask", writes={"a.txt": "a1\n"})
        inner = [e for e in session.get_branch() if e.id != u1]
        assert inner, "the turn should have produced more entries"
        for entry in inner:
            assert rv.boundary_for_entry(session, entry.id) == u1
        assert rv.boundary_for_entry(session, u1) == u1
        assert rv.boundary_for_entry(session, "nope") is None

    def test_revert_to_commits_immediately_when_asked(
        self, project: Path, store: SnapshotStore
    ) -> None:
        """`/tree` jumps and lands; `/undo` stages until the next prompt."""
        session, _u0, u1, u2 = self._three_turns(project, store)
        assert (project / "a.txt").read_text() == "a3\n"

        rv.revert_to(session, u1, store=store, cwd=str(project), commit_now=True)
        assert (project / "a.txt").read_text() == "a1\n"
        assert rv.current_state(session) is None, "a jump leaves nothing staged"
        assert u2 not in [e.id for e in session.get_branch()], "u2 left the branch"

        # Staged (the /undo default) keeps /redo alive. Staging at the *last*
        # turn means "at the tip", where /redo clears instead of stepping, so
        # stage one turn earlier to get a real forward step.
        session2, _a, b1, _b2 = self._three_turns(project, store)
        rv.revert_to(session2, b1, store=store, cwd=str(project))
        assert rv.current_state(session2) is not None
        assert rv.next_boundary(session2) is not None

    def test_unknown_boundary_is_refused(self, project: Path, store: SnapshotStore) -> None:
        session = _session(project)
        _turn(session, store, project, "hello")
        with pytest.raises(rv.RevertError):
            rv.stage(session, "not-a-real-entry", store=store, cwd=str(project))

    def test_non_git_project_is_refused(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
        plain = tmp_path / "plain"
        plain.mkdir()
        session = _session(plain)
        entry = session.append_message(UserMessage(content="hi"))
        with pytest.raises(rv.RevertError, match="git repository"):
            rv.stage(session, entry, cwd=str(plain))

    def test_state_survives_a_json_round_trip(self, project: Path, store: SnapshotStore) -> None:
        session, _u0, _u1, u2 = self._three_turns(project, store)
        state = rv.stage(session, u2, store=store, cwd=str(project))
        restored = rv.RevertState.from_json(state.to_json())
        assert restored is not None
        assert restored.boundary_id == state.boundary_id
        assert [d.path for d in restored.files] == [d.path for d in state.files]

    def test_corrupt_state_json_is_ignored(self) -> None:
        assert rv.RevertState.from_json("not json") is None
        assert rv.RevertState.from_json("{}") is None


def _u0_boundary(session: Session) -> str:
    """Rewind all the way to the first turn's user message."""
    return rv.user_messages(session)[0].id


class TestNavigation:
    def test_no_user_messages_means_nothing_to_undo(self, project: Path) -> None:
        session = _session(project)
        assert rv.previous_boundary(session) is None
        assert rv.next_boundary(session) is None

    def test_redo_without_a_revert_is_a_noop(self, project: Path, store: SnapshotStore) -> None:
        session = _session(project)
        _turn(session, store, project, "just one ask")
        assert rv.current_state(session) is None
        assert rv.next_boundary(session) is None

    def test_describe_is_human_readable(self, project: Path, store: SnapshotStore) -> None:
        session = _session(project)
        u1 = _turn(session, store, project, "second ask", writes={"a.txt": "a1\n"})
        state = rv.stage(session, u1, store=store, cwd=str(project))
        text = rv.describe(state)
        assert "second ask" in text
        assert "1 files" in text

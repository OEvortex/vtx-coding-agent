"""`/undo` and `/redo` driven through the real Textual app."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from vtx.ai.agent.revert import commit as _commit
from vtx.ai.agent.revert import current_state as _current_state
from vtx.ai.agent.revert import record_turn_snapshot
from vtx.ai.agent.session import Session
from vtx.ai.agent.snapshot import SnapshotStore, get_store
from vtx.core.types import FileChanges, TextContent, ToolResultMessage, UserMessage
from vtx.tui.app import Vtx
from vtx.tui.chat import ChatLog


@pytest.fixture()
def project(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    proj = tmp_path / "proj"
    proj.mkdir()
    subprocess.run(["git", "init", "-q", "."], cwd=proj, check=True, capture_output=True)
    (proj / "a.txt").write_text("a0\n")
    subprocess.run(["git", "add", "-A"], cwd=proj, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=proj,
        check=True,
        capture_output=True,
    )
    return proj


def _turn(
    session: Session,
    store: SnapshotStore,
    proj: Path,
    prompt: str,
    content: str,
    *,
    report: bool = True,
    path: str = "a.txt",
) -> str:
    """One bracketed turn, matching what the runtime records."""
    start = store.capture()
    entry = session.append_message(UserMessage(content=prompt))
    (proj / path).write_text(content)
    if report:
        session.append_message(
            ToolResultMessage(
                tool_name="edit",
                tool_call_id=f"call-{content}",
                content=[TextContent(text="ok")],
                file_changes=FileChanges(path=str(proj / path), added=1, removed=1),
            )
        )
    record_turn_snapshot(session, start, cwd=str(proj))
    return entry


def _seed_turns(app: Vtx, proj: Path) -> tuple[Session, list[str]]:
    """Three turns that edit a.txt."""
    session = app._runtime.session
    assert session is not None
    store = get_store(str(proj))
    boundaries = [
        _turn(session, store, proj, f"ask {i}", f"{content}\n")
        for i, content in enumerate(["a1", "a2", "a3"], start=1)
    ]
    return session, boundaries


def _chat_lines(app: Vtx) -> list[str]:
    chat = app.query_one("#chat-log", ChatLog)
    return [str(getattr(child, "content", "")) for child in chat.children]


@pytest.mark.asyncio
async def test_undo_then_redo_restores_and_reapplies_files(project: Path) -> None:
    app = Vtx(cwd=str(project))
    async with app.run_test(size=(100, 30)) as pilot:
        _seed_turns(app, project)
        assert (project / "a.txt").read_text() == "a3\n"

        app._handle_command("/undo")
        await pilot.pause()
        assert (project / "a.txt").read_text() == "a2\n"
        lines = _chat_lines(app)
        assert any("Reverted to" in line for line in lines), lines[-3:]
        assert any("/redo" in line for line in lines), "the marker must advertise /redo"

        app._handle_command("/undo")
        await pilot.pause()
        assert (project / "a.txt").read_text() == "a1\n"

        app._handle_command("/redo")
        await pilot.pause()
        assert (project / "a.txt").read_text() == "a2\n"

        # Redo at the newest turn clears the revert instead of stepping further.
        app._handle_command("/redo")
        await pilot.pause()
        assert (project / "a.txt").read_text() == "a3\n"
        app._handle_command("/redo")
        await pilot.pause()
        assert any("Nothing to redo" in line for line in _chat_lines(app))


@pytest.mark.asyncio
async def test_undo_restores_files_changed_without_tool_reports(project: Path) -> None:
    """The reported failure: bash/ipython edits reported by no tool.

    The turn below changes two files and emits no ``file_changes`` at all. A
    revert that trusted tool reports would restore nothing and leave the
    worktree dirty.
    """
    app = Vtx(cwd=str(project))
    async with app.run_test(size=(100, 30)) as pilot:
        session = app._runtime.session
        assert session is not None
        store = get_store(str(project))
        _turn(
            session, store, project, "bump version", "1.2.1\n", report=False, path="pyproject.toml"
        )
        _turn(session, store, project, "lock", "locked\n", report=False, path="uv.lock")
        assert (project / "pyproject.toml").exists()

        app._handle_command("/undo")
        await pilot.pause()
        assert not (project / "uv.lock").exists(), "unreported edit must still be reverted"
        assert (project / "pyproject.toml").exists(), "only the last turn is undone"

        app._handle_command("/undo")
        await pilot.pause()
        assert not (project / "pyproject.toml").exists()


@pytest.mark.asyncio
async def test_undo_to_the_start_and_back_is_graceful(project: Path) -> None:
    app = Vtx(cwd=str(project))
    async with app.run_test(size=(100, 30)) as pilot:
        _seed_turns(app, project)
        for _ in range(4):
            app._handle_command("/undo")
            await pilot.pause()
        assert (project / "a.txt").read_text() == "a0\n"
        assert any("Nothing left to undo" in line for line in _chat_lines(app))


@pytest.mark.asyncio
async def test_undo_is_refused_while_the_agent_is_working(project: Path) -> None:
    app = Vtx(cwd=str(project))
    async with app.run_test(size=(100, 30)) as pilot:
        _seed_turns(app, project)
        app._is_running = True
        try:
            app._handle_command("/undo")
            await pilot.pause()
        finally:
            app._is_running = False
        assert (project / "a.txt").read_text() == "a3\n", "must not revert mid-turn"
        assert any("Cannot undo" in line for line in _chat_lines(app))


@pytest.mark.asyncio
async def test_tree_jump_restores_files_too(project: Path) -> None:
    """A tree jump must leave the transcript and the worktree agreeing.

    Regression: `/tree` re-pointed the session leaf but never touched disk, so
    picking an old point showed a past conversation over a dirty worktree.
    """
    app = Vtx(cwd=str(project))
    async with app.run_test(size=(100, 30)) as pilot:
        session, boundaries = _seed_turns(app, project)
        assert (project / "a.txt").read_text() == "a3\n"

        # Jump back to the first turn's prompt, as the tree selector would.
        app._revert_tree_to(boundaries[0], app.query_one("#chat-log", ChatLog))
        await pilot.pause()

        assert (project / "a.txt").read_text() == "a0\n", (project / "a.txt").read_text()
        assert boundaries[2] not in [e.id for e in session.get_branch()]
        # A jump is immediate: nothing left staged for /redo.
        assert _current_state(session) is None

    # The branch is still reachable, so the jump was not destructive.
    assert session.get_branch(boundaries[2])


@pytest.mark.asyncio
async def test_tree_jump_to_current_position_is_a_noop(project: Path) -> None:
    app = Vtx(cwd=str(project))
    async with app.run_test(size=(100, 30)) as pilot:
        session, boundaries = _seed_turns(app, project)
        app._revert_tree_to(boundaries[2], app.query_one("#chat-log", ChatLog))
        await pilot.pause()
        assert (project / "a.txt").read_text() == "a3\n", "must not touch the newest turn"
        assert _current_state(session) is None


@pytest.mark.asyncio
async def test_undo_without_git_reports_a_clear_error(tmp_path: Path, monkeypatch) -> None:
    """A non-git project has no snapshots; the command must say so, not crash."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "a.txt").write_text("a0\n")

    app = Vtx(cwd=str(plain))
    async with app.run_test(size=(100, 30)) as pilot:
        session = app._runtime.session
        assert session is not None
        session.append_message(UserMessage(content="hello"))
        app._handle_command("/undo")
        await pilot.pause()
        assert (plain / "a.txt").read_text() == "a0\n"
        assert any("git repository" in line for line in _chat_lines(app)), _chat_lines(app)[-3:]


@pytest.mark.asyncio
async def test_resumed_session_undo_works_after_a_new_turn(project: Path) -> None:
    """Turns already on disk predate snapshotting.

    They can only rewind the conversation; the first turn that runs after the
    resume records its own bracket, and from then on files are restored.
    """
    app = Vtx(cwd=str(project))
    async with app.run_test(size=(100, 30)) as pilot:
        session = app._runtime.session
        assert session is not None
        store = get_store(str(project))

        # Turns that "came from disk": no snapshot record at all.
        for index in (1, 2):
            session.append_message(UserMessage(content=f"pre-resume ask {index}"))
            (project / "a.txt").write_text(f"a{index}\n")
            session.append_message(
                ToolResultMessage(
                    tool_name="edit",
                    tool_call_id=f"pre-{index}",
                    content=[TextContent(text="ok")],
                    file_changes=FileChanges(path=str(project / "a.txt"), added=1, removed=1),
                )
            )

        _turn(session, store, project, "post-resume ask", "a3\n")
        assert (project / "a.txt").read_text() == "a3\n"

        # The post-resume turn restores files.
        app._handle_command("/undo")
        await pilot.pause()
        assert (project / "a.txt").read_text() == "a2\n", (project / "a.txt").read_text()
        assert any("Reverted to" in line for line in _chat_lines(app))

        # Committing drops the recorded turn from the branch, so undoing into
        # pre-resume territory has no record left and degrades to a
        # conversation-only rewind rather than an error.
        _commit(session)
        app._handle_command("/undo")
        await pilot.pause()
        lines = _chat_lines(app)
        assert any("conversation only" in line for line in lines), lines[-3:]
        assert not any("no snapshot recorded" in line for line in lines)

"""`/undo` and `/redo` driven through the real Textual app."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from textual.app import App  # noqa: F401  (imported for the fixture plugin)

from vtx.ai.agent.revert import record_turn_snapshot
from vtx.ai.agent.session import Session
from vtx.ai.agent.snapshot import get_store
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


def _seed_turns(app: Vtx, proj: Path) -> tuple[Session, list[str]]:
    """Append three user/assistant turns that edit a.txt."""
    session = app._runtime.session
    assert session is not None
    store = get_store(str(proj))
    boundaries: list[str] = []
    for index, content in enumerate(["a1", "a2", "a3"], start=1):
        entry = session.append_message(UserMessage(content=f"ask {index}"))
        record_turn_snapshot(session, store.capture())
        boundaries.append(entry)
        (proj / "a.txt").write_text(f"{content}\n")
        session.append_message(
            ToolResultMessage(
                tool_name="edit",
                tool_call_id=f"call-{index}",
                content=[TextContent(text="ok")],
                file_changes=FileChanges(path="a.txt", added=1, removed=1),
            )
        )
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
async def test_undo_without_git_rewinds_conversation_only(tmp_path: Path, monkeypatch) -> None:
    """A non-git project has no snapshots; the command must degrade, not crash."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "a.txt").write_text("a0\n")

    app = Vtx(cwd=str(plain))
    async with app.run_test(size=(100, 30)) as pilot:
        session = app._runtime.session
        assert session is not None
        store = get_store(str(plain))
        for index in (1, 2):
            session.append_message(UserMessage(content=f"ask {index}"))
            record_turn_snapshot(session, store.capture())
            (plain / "a.txt").write_text(f"a{index}\n")

        app._handle_command("/undo")
        await pilot.pause()
        lines = _chat_lines(app)
        # No tree to restore, so the conversation rewinds and says so plainly
        # instead of failing outright.
        assert any("conversation only" in line for line in lines), lines[-3:]
        assert not any("no snapshot recorded" in line for line in lines)
        assert (plain / "a.txt").read_text() == "a2\n"


@pytest.mark.asyncio
async def test_resumed_session_undo_works_after_a_new_turn(project: Path) -> None:
    """Regression: a resumed session had no undo target at all.

    Turns already on disk predate snapshotting, so they can only rewind the
    conversation. The first turn that runs after the resume records its own
    snapshot at turn start, and from then on /undo restores files properly.
    """
    app = Vtx(cwd=str(project))
    async with app.run_test(size=(100, 30)) as pilot:
        session = app._runtime.session
        assert session is not None
        store = get_store(str(project))

        # Turns that "came from disk": appended without any snapshot entry,
        # which is exactly what a session recorded before this feature looks
        # like once it is resumed.
        for index in (1, 2):
            session.append_message(UserMessage(content=f"pre-resume ask {index}"))
            (project / "a.txt").write_text(f"a{index}\n")
            session.append_message(
                ToolResultMessage(
                    tool_name="edit",
                    tool_call_id=f"pre-{index}",
                    content=[TextContent(text="ok")],
                    file_changes=FileChanges(path="a.txt", added=1, removed=1),
                )
            )

        # A turn that runs after the resume: the turn-start capture is what
        # makes it undoable.
        session.append_message(UserMessage(content="post-resume ask"))
        record_turn_snapshot(session, store.capture())
        (project / "a.txt").write_text("a3\n")
        session.append_message(
            ToolResultMessage(
                tool_name="edit",
                tool_call_id="post",
                content=[TextContent(text="ok")],
                file_changes=FileChanges(path="a.txt", added=1, removed=1),
            )
        )
        assert (project / "a.txt").read_text() == "a3\n"

        # The post-resume turn restores files.
        app._handle_command("/undo")
        await pilot.pause()
        assert (project / "a.txt").read_text() == "a2\n", (project / "a.txt").read_text()
        assert any("Reverted to" in line for line in _chat_lines(app))

        # Undoing further back reaches a pre-resume turn: conversation only,
        # with no dead-end error.
        app._handle_command("/undo")
        await pilot.pause()
        lines = _chat_lines(app)
        assert any("conversation only" in line for line in lines), lines[-3:]
        assert not any("no snapshot recorded" in line for line in lines)

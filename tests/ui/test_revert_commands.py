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
        store = get_store(str(plain))
        for index in (1, 2):
            session.append_message(UserMessage(content=f"ask {index}"))
            record_turn_snapshot(session, store.capture())
            (plain / "a.txt").write_text(f"a{index}\n")

        app._handle_command("/undo")
        await pilot.pause()
        assert (plain / "a.txt").read_text() == "a2\n", "nothing to restore, file untouched"
        lines = _chat_lines(app)
        assert any("no snapshot recorded" in line or "Not a git" in line for line in lines), lines[
            -3:
        ]

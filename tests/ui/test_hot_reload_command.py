"""End-to-end ``/reload`` against a live Textual app.

The unit tests in ``test_hot_reload.py`` cover the loader and the runtime
rebind. This exercises the command itself: that ``/reload`` actually rebuilds
the tool set from a newly added extension file, keeps the conversation, and
refuses to run mid-turn.

The app here is a trimmed stand-in rather than the real ``Vtx`` -- the command
only needs a chat log, a runtime, a cwd, and a session, and building the full
app would drag in config, providers, and the model catalog.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.app import App, ComposeResult

from vtx.tui.chat import ChatLog
from vtx.tui.commands.reload import ReloadCommands
from vtx.tui.input import InputBox

_EXT = """\
from pydantic import BaseModel

from vtx.ai.agent.tools.base import BaseTool, ToolResult

VERSION = {version!r}


class P(BaseModel):
    pass


def register(api):
    class T(BaseTool):
        name = "probe"
        params = P
        mutating = False
        description = "probe"

        async def execute(self, params, cancel_event=None):
            return ToolResult(success=True, result=VERSION)

    api.register_tool("probe", "probe", {{}}, execute=T().execute)
"""


def _write_ext(directory: Path, version: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "probe_ext.py"
    path.write_text(_EXT.format(version=version), encoding="utf-8")
    return path


class _ReloadApp(ReloadCommands, App):
    """Minimal host for the reload command."""

    CSS = "ChatLog { height: 1fr; } InputBox { height: 3; }"

    def __init__(self, cwd: Path, runtime, session=None) -> None:
        super().__init__()
        self._cwd = str(cwd)
        self._runtime = runtime
        self._session = session
        self._is_running = False
        self._tools: list = []
        self._sync_calls = 0
        self._loaded_extensions = None

    def compose(self) -> ComposeResult:
        yield ChatLog(id="chat-log")
        yield InputBox(cwd=self._cwd, id="input-box")

    def _sync_slash_commands(self) -> None:
        self._sync_calls += 1


@pytest.fixture
def reload_app(tmp_path: Path, monkeypatch):
    from vtx.ai.agent.runtime import ConversationRuntime
    from vtx.ai.agent.session import Session

    ext_dir = tmp_path / "exts"
    workdir = tmp_path / "work"
    workdir.mkdir(parents=True, exist_ok=True)
    runtime = ConversationRuntime(cwd=str(workdir), tools=[])
    runtime.session = Session("s-reload", str(workdir), persist=False)

    # Point extension discovery at the temp dir. The command reloads config
    # first, which would discard an in-memory patch, so reload_config is stubbed
    # out here; it has its own coverage and is not what these tests are about.
    import vtx.ai.config as config_mod
    from vtx.tui.commands import reload as reload_mod

    monkeypatch.setattr(reload_mod, "reload_config", lambda: None, raising=True)
    monkeypatch.setattr(config_mod.config._parsed, "extensions", [str(ext_dir)], raising=False)
    app = _ReloadApp(workdir, runtime)
    app.ext_dir = ext_dir
    return app, runtime, ext_dir


class TestReloadCommand:
    @pytest.mark.asyncio
    async def test_picks_up_a_newly_added_extension(self, reload_app):
        app, runtime, ext_dir = reload_app
        async with app.run_test() as pilot:
            # Nothing registered yet.
            _write_ext(ext_dir, "v1")
            await pilot.pause()

            app._handle_reload_command("")
            await pilot.pause()

            assert "probe" in [t.name for t in runtime.tools], (
                "the extension file was on disk before /reload ran, so its tool "
                "must be in the rebuilt set"
            )

    @pytest.mark.asyncio
    async def test_reload_resyncs_the_slash_command_list(self, reload_app):
        app, _runtime, _ext_dir = reload_app
        async with app.run_test() as pilot:
            await pilot.pause()
            before = app._sync_calls
            app._handle_reload_command("")
            await pilot.pause()
            assert app._sync_calls == before + 1

    @pytest.mark.asyncio
    async def test_refuses_while_a_turn_is_running(self, reload_app):
        app, runtime, _ext_dir = reload_app
        async with app.run_test() as pilot:
            await pilot.pause()
            app._is_running = True
            tools_before = [t.name for t in runtime.tools]

            app._handle_reload_command("")
            await pilot.pause()

            assert [t.name for t in runtime.tools] == tools_before
            app._is_running = False

    @pytest.mark.asyncio
    async def test_keeps_the_conversation(self, reload_app):
        app, runtime, _ext_dir = reload_app
        async with app.run_test() as pilot:
            await pilot.pause()
            session = runtime.session
            app._handle_reload_command("")
            await pilot.pause()
            assert runtime.session is session

    @pytest.mark.asyncio
    async def test_reload_is_repeatable(self, reload_app):
        """A second reload must be as safe as the first."""
        app, _runtime, ext_dir = reload_app
        async with app.run_test() as pilot:
            await pilot.pause()
            _write_ext(ext_dir, "v1")
            for _ in range(3):
                app._handle_reload_command("")
                await pilot.pause()
            assert "probe" in [t.name for t in app._runtime.tools]

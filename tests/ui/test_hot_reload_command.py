"""Settings edits must take effect on ``/reload``, not merely be re-read.

The headline case is the one that is easy to get wrong: flip ``mode:`` in
``config.yml`` and reload. A reload that only re-read the file would leave the
running agent with its old tool surface while the system prompt started
describing a different one — the model would be told it had a REPL it did not
have, or told it had surgical tools it no longer had.

These run against a real on-disk ``config.yml`` in a temp ``XDG_CONFIG_HOME``,
so ``reload_config()`` does real work rather than being stubbed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from textual.app import App, ComposeResult

from vtx.tui.chat import ChatLog
from vtx.tui.commands.reload import ReloadCommands
from vtx.tui.input import InputBox
from vtx.tui.widgets import InfoBar


class _ReloadApp(ReloadCommands, App):
    """Minimal host for the reload command.

    The real ``Vtx`` is not built here: the command only needs a chat log, a
    runtime, a cwd, and a session, and standing up the full app would drag in
    the model catalog and provider config for no extra coverage.
    """

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
        self.applied_themes: list[str] = []

    def compose(self) -> ComposeResult:
        yield ChatLog(id="chat-log")
        yield InputBox(cwd=self._cwd, id="input-box")
        yield InfoBar(cwd=self._cwd, model="test-model", id="compact-footer")

    def _sync_slash_commands(self) -> None:
        self._sync_calls += 1

    def _apply_theme(self, theme_id: str) -> None:
        self.applied_themes.append(theme_id)


@pytest.fixture
def config_file(tmp_path: Path, monkeypatch):
    """A real config.yml in a sandboxed config dir.

    ``get_config_dir`` prefers ``XDG_CONFIG_HOME``, so pointing it at a temp dir
    sandboxes the whole config path without stubbing the loader -- which is the
    part most likely to drift from production behaviour.
    """
    from importlib import resources

    import vtx.ai.config as config_mod

    xdg = tmp_path / "xdg"
    (xdg / "vtx").mkdir(parents=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    config_mod.reset_config()
    path = xdg / "vtx" / "config.yml"
    defaults = yaml.safe_load(
        resources.files("vtx.ai.defaults").joinpath("config.yml").read_text("utf-8")
    )
    path.write_text(yaml.safe_dump(defaults), encoding="utf-8")
    yield path
    config_mod.reset_config()


def _edit_config(path: Path, **overrides) -> None:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    for dotted, value in overrides.items():
        section, _, key = dotted.partition(".")
        if key:
            data.setdefault(section, {})[key] = value
        else:
            data[section] = value
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


@pytest.fixture
def reload_app(tmp_path: Path):
    from vtx.ai.agent.runtime import ConversationRuntime
    from vtx.ai.agent.session import Session

    workdir = tmp_path / "work"
    workdir.mkdir(parents=True, exist_ok=True)
    runtime = ConversationRuntime(cwd=str(workdir), tools=[])
    runtime.session = Session("s-reload", str(workdir), persist=False)
    return _ReloadApp(workdir, runtime), runtime


def _reload(app) -> None:
    app._handle_reload_command("")


class TestModeSwitch:
    """A `mode:` edit must change the live tool surface, not just the prompt."""

    @pytest.mark.asyncio
    async def test_tool_first_keeps_the_surgical_surface(self, config_file, reload_app):
        app, runtime = reload_app
        _edit_config(config_file, mode="tool_first")
        async with app.run_test() as pilot:
            await pilot.pause()
            _reload(app)
            await pilot.pause()
            names = {t.name for t in runtime.tools}
            assert {"read", "bash", "edit", "write"} <= names

    @pytest.mark.asyncio
    async def test_the_conversation_survives_a_reload(self, config_file, reload_app):
        app, runtime = reload_app
        session = runtime.session
        _edit_config(config_file, mode="tool_first")
        async with app.run_test() as pilot:
            await pilot.pause()
            _reload(app)
            await pilot.pause()
            assert runtime.session is session

    @pytest.mark.asyncio
    async def test_the_system_prompt_describes_the_actual_surface(self, config_file, reload_app):
        """Otherwise the model is told about tools it does not have."""
        app, runtime = reload_app
        _edit_config(config_file, mode="tool_first")
        async with app.run_test() as pilot:
            await pilot.pause()
            _reload(app)
            await pilot.pause()
            prompt = runtime.resolve_system_prompt()
            assert "ipython" not in prompt
            assert "REPL" not in prompt


class TestThemeAndChrome:
    @pytest.mark.asyncio
    async def test_theme_edit_is_applied(self, config_file, reload_app):
        app, _runtime = reload_app
        _edit_config(config_file, **{"ui.theme": "ayu"})
        async with app.run_test() as pilot:
            await pilot.pause()
            _reload(app)
            await pilot.pause()
            assert app.applied_themes == ["ayu"]

    @pytest.mark.asyncio
    async def test_unchanged_theme_is_not_reapplied(self, config_file, reload_app):
        """A stylesheet rebuild on every reload would flicker the whole UI."""
        app, _runtime = reload_app
        _edit_config(config_file, **{"ui.theme": "ayu"})
        async with app.run_test() as pilot:
            await pilot.pause()
            _reload(app)
            await pilot.pause()
            app.applied_themes.clear()
            _reload(app)
            await pilot.pause()
            assert app.applied_themes == []

    @pytest.mark.asyncio
    async def test_permission_edit_reaches_the_footer(self, config_file, reload_app):
        app, _runtime = reload_app
        _edit_config(config_file, **{"permissions.mode": "auto"})
        async with app.run_test() as pilot:
            await pilot.pause()
            _reload(app)
            await pilot.pause()
            info = pilot.app.query_one("#compact-footer", InfoBar)
            assert info._permission_mode == "auto"


class TestReloadIsSafe:
    @pytest.mark.asyncio
    async def test_refuses_while_a_turn_is_running(self, config_file, reload_app):
        app, runtime = reload_app
        async with app.run_test() as pilot:
            await pilot.pause()
            _reload(app)
            await pilot.pause()
            before = [t.name for t in runtime.tools]
            app._is_running = True
            _reload(app)
            await pilot.pause()
            assert [t.name for t in runtime.tools] == before

    @pytest.mark.asyncio
    async def test_reload_is_repeatable(self, config_file, reload_app):
        app, runtime = reload_app
        _edit_config(config_file, mode="tool_first")
        async with app.run_test() as pilot:
            await pilot.pause()
            _reload(app)
            await pilot.pause()
            first = [t.name for t in runtime.tools]
            for _ in range(3):
                _reload(app)
                await pilot.pause()
            # Reloading repeatedly must not accumulate or drop tools; the
            # surface it converges to is the point.
            assert [t.name for t in runtime.tools] == first
            assert len(first) > 1

    @pytest.mark.asyncio
    async def test_picks_up_a_newly_added_extension(self, tmp_path, monkeypatch, reload_app):

        app, runtime = reload_app
        ext_dir = tmp_path / "exts"
        ext_dir.mkdir(parents=True, exist_ok=True)
        path = ext_dir / "probe_ext.py"
        path.write_text(
            "from pydantic import BaseModel\n"
            "from vtx.ai.agent.tools.base import BaseTool, ToolResult\n"
            "class P(BaseModel):\n    pass\n"
            "def register(api):\n"
            "    class T(BaseTool):\n"
            "        name = 'probe'\n"
            "        params = P\n"
            "        mutating = False\n"
            "        description = 'probe'\n"
            "        async def execute(self, params, cancel_event=None):\n"
            "            return ToolResult(success=True, result='v1')\n"
            "    api.register_tool('probe', 'probe', {}, execute=T().execute)\n",
            encoding="utf-8",
        )

        import vtx.ai.config as config_mod
        from vtx.tui.commands import reload as reload_mod

        monkeypatch.setattr(reload_mod, "reload_config", lambda: None)
        monkeypatch.setattr(config_mod.config._parsed, "extensions", [str(ext_dir)], raising=False)

        async with app.run_test() as pilot:
            await pilot.pause()
            _reload(app)
            await pilot.pause()
            assert "probe" in [t.name for t in runtime.tools]

            # An edit to that extension is picked up by the next reload. Both
            # reloads run in one session because a Textual app cannot be
            # mounted twice.
            async def probe_version() -> str:
                tool = next(t for t in runtime.tools if t.name == "probe")
                return str((await tool.execute(tool.params())).result)

            assert await probe_version() == "v1"

            path.write_text(
                path.read_text(encoding="utf-8").replace("'v1'", "'v2'"), encoding="utf-8"
            )
            _reload(app)
            await pilot.pause()
            assert await probe_version() == "v2"

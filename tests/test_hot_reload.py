"""``/reload`` — hot reload without restarting vtx.

Two properties are worth more than the feature list:

- **A reload means the new code is running.** The loader used to hand the file
  to ``spec.loader.exec_module``, which validates cached bytecode against the
  source's mtime *and size*. An edit that kept the file the same length was
  served from ``__pycache__``, so a reload reported success while running the
  previous version. Same for a package extension's submodules, which resolve
  through the normal finder and stayed stale forever.
- **A reload never silently resets a choice the user made.** The active agent,
  model, provider, thinking level, and the conversation all survive; only what
  the agent is *made of* is replaced.

The tests assert observable behaviour — what a registered tool returns — rather
than the loader's internals, because that is the property a user depends on.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from vtx.ai.agent.extensions import (
    EventBus,
    ExtensionLoadError,
    invalidate_extension_bytecode,
    load_extension,
    load_for_runtime,
)
from vtx.tui.autocomplete import DEFAULT_COMMANDS

# A minimal but real extension: a tool whose return value comes from the file,
# so a reload is observable as a changed tool result.
_EXT_TEMPLATE = """\
from pydantic import BaseModel

from vtx.ai.agent.tools.base import BaseTool, ToolResult

VERSION = {version!r}
{probe_import}

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


def _write_single(path: Path, version: str) -> None:
    path.write_text(_EXT_TEMPLATE.format(version=version, probe_import=""), encoding="utf-8")


def _write_package(pkg: Path, version: str) -> None:
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "impl.py").write_text(f"VERSION = {version!r}\n", encoding="utf-8")
    (pkg / "__init__.py").write_text(
        _EXT_TEMPLATE.format(version="unused", probe_import="from .impl import VERSION"),
        encoding="utf-8",
    )


def _load(path: Path, *, fresh: bool = False):
    return load_extension(
        path,
        bus=EventBus(),
        cwd=str(path.parent),
        session_file=None,
        config_dir=path.parent,
        fresh=fresh,
    )


def _probe(ext) -> str:
    tool = ext.tools["probe"]
    return str(asyncio.run(tool.execute(tool.params())).result)


def _header(cwd: Path, *, system_prompt: str | None, tools: list[str] | None):
    from vtx.ai.agent.session import SessionHeader

    return SessionHeader(
        id="s1", timestamp="t", cwd=str(cwd), system_prompt=system_prompt, tools=tools
    )


def _invalidate_modules() -> None:
    """Mirror of ReloadCommands._invalidate_extension_modules."""
    import importlib
    import sys

    importlib.invalidate_caches()
    for name in [n for n in sys.modules if n.startswith("vtx_ext_")]:
        sys.modules.pop(name, None)


class TestEditedCodeTakesEffect:
    def test_single_file_edit_is_picked_up(self, tmp_path: Path):
        """Same byte length on purpose: this is what a stale .pyc hides."""
        ext = tmp_path / "probe_ext.py"
        _write_single(ext, "v1")
        assert _probe(_load(ext)) == "v1"

        _write_single(ext, "v2")
        assert _probe(_load(ext)) == "v2"

    def test_package_submodule_edit_is_stale_without_invalidation(self, tmp_path: Path):
        """Documents why the reload has to purge sys.modules at all."""
        pkg = tmp_path / "probepkg"
        _write_package(pkg, "p1")
        assert _probe(_load(pkg / "__init__.py")) == "p1"

        (pkg / "impl.py").write_text("VERSION = 'p2'\n", encoding="utf-8")
        assert _probe(_load(pkg / "__init__.py")) == "p1"

    def test_purging_modules_and_bytecode_fixes_the_package_case(self, tmp_path: Path):
        pkg = tmp_path / "probepkg2"
        _write_package(pkg, "p1")
        assert _probe(_load(pkg / "__init__.py")) == "p1"

        (pkg / "impl.py").write_text("VERSION = 'p2'\n", encoding="utf-8")
        _invalidate_modules()
        assert _probe(_load(pkg / "__init__.py", fresh=True)) == "p2"

    def test_newly_added_submodule_becomes_importable(self, tmp_path: Path):
        pkg = tmp_path / "probepkg3"
        pkg.mkdir()
        (pkg / "__init__.py").write_text(
            _EXT_TEMPLATE.format(version="base", probe_import=""), encoding="utf-8"
        )
        assert _probe(_load(pkg / "__init__.py")) == "base"

        (pkg / "added.py").write_text("VERSION = 'added'\n", encoding="utf-8")
        (pkg / "__init__.py").write_text(
            _EXT_TEMPLATE.format(version="unused", probe_import="from .added import VERSION"),
            encoding="utf-8",
        )
        _invalidate_modules()
        assert _probe(_load(pkg / "__init__.py", fresh=True)) == "added"

    def test_invalidate_bytecode_only_touches_packages(self, tmp_path: Path):
        single = tmp_path / "solo.py"
        _write_single(single, "v1")
        invalidate_extension_bytecode(single)  # no-op, must not raise
        single.write_text("raise SystemExit('must not be executed')\n", encoding="utf-8")
        invalidate_extension_bytecode(single)
        # Still loadable, i.e. we did not delete anything next to it.
        _write_single(single, "v3")
        assert _probe(_load(single)) == "v3"

    def test_failed_import_removes_the_half_built_module(self, tmp_path: Path):
        """A syntax error must not leave a broken module in sys.modules that a
        later reload would then bind to."""
        ext = tmp_path / "broken_ext.py"
        ext.write_text("def register(api)\n    pass\n", encoding="utf-8")
        with pytest.raises(ExtensionLoadError):
            _load(ext)

        _write_single(ext, "recovered")
        assert _probe(_load(ext)) == "recovered"


class TestReloadIsWired:
    def test_command_is_registered(self):
        assert any(c.name == "reload" for c in DEFAULT_COMMANDS)

    def test_dispatch_reaches_the_handler(self):
        from vtx.tui.commands import CommandsMixin

        assert hasattr(CommandsMixin, "_handle_reload_command")
        assert any(base.__name__ == "ReloadCommands" for base in CommandsMixin.__mro__)

    def test_load_for_runtime_accepts_fresh(self, tmp_path: Path):
        loaded = load_for_runtime(cwd=str(tmp_path), auto_discover=False, fresh=True)
        assert loaded.errors == []


class TestRuntimeRebind:
    def test_rebind_swaps_tools_and_keeps_the_session(self, tmp_path: Path):
        from vtx.ai.agent.runtime import ConversationRuntime
        from vtx.ai.agent.tools import get_tools_with_extensions

        runtime = ConversationRuntime(cwd=str(tmp_path), tools=[])
        from vtx.ai.agent.session import Session

        session = Session("s-keep", str(tmp_path), persist=False)
        runtime.session = session

        replacement = get_tools_with_extensions(["read", "bash"])
        runtime.rebind_resources(tools=replacement)

        assert [t.name for t in runtime.tools] == ["read", "bash"]
        assert runtime.session is session, "a reload must not drop the conversation"

    def test_rebind_clears_the_cached_header_prompt(self, tmp_path: Path):
        """resolve_system_prompt prefers the header prompt over a fresh build, so
        a stale one would keep being sent after a reload."""
        from vtx.ai.agent.runtime import ConversationRuntime
        from vtx.ai.agent.session import Session

        runtime = ConversationRuntime(cwd=str(tmp_path), tools=[])
        session = Session("s-reload", str(tmp_path), persist=False)
        session._header = _header(tmp_path, system_prompt="STALE PROMPT", tools=["read"])
        runtime.session = session

        runtime.rebind_resources()

        assert session.system_prompt is None

    def test_rebind_records_the_new_tool_names(self, tmp_path: Path):
        from vtx.ai.agent.runtime import ConversationRuntime
        from vtx.ai.agent.session import Session
        from vtx.ai.agent.tools import get_tools_with_extensions

        runtime = ConversationRuntime(cwd=str(tmp_path), tools=[])
        session = Session("s-reload", str(tmp_path), persist=False)
        session._header = _header(tmp_path, system_prompt=None, tools=["read"])
        runtime.session = session

        runtime.rebind_resources(tools=get_tools_with_extensions(["read", "bash"]))
        assert session.tools == ["read", "bash"]

    def test_set_system_prompt_is_a_noop_without_a_header(self, tmp_path: Path):
        from vtx.ai.agent.session import Session

        Session("s-reload", str(tmp_path), persist=False).set_system_prompt("x")


class TestModeToolPolicy:
    """One runtime mode remains, so the policy is a no-op. It is still tested
    because ``/reload`` calls it after re-reading config, and a future mode
    would reshape the live tool set from exactly this method."""

    def _runtime(self, tmp_path: Path):
        from vtx.ai.agent.runtime import ConversationRuntime
        from vtx.ai.agent.tools import get_tools_with_extensions

        runtime = ConversationRuntime(cwd=str(tmp_path), tools=[])
        runtime.tools = get_tools_with_extensions()
        return runtime

    def test_policy_leaves_the_surface_alone(self, tmp_path: Path):
        runtime = self._runtime(tmp_path)
        before = [t.name for t in runtime.tools]
        runtime.apply_mode_tool_policy()
        assert [t.name for t in runtime.tools] == before

    def test_rebind_keeps_the_rebuilt_surface(self, tmp_path: Path):
        from vtx.ai.agent.runtime import ConversationRuntime
        from vtx.ai.agent.tools import get_tools_with_extensions

        runtime = ConversationRuntime(cwd=str(tmp_path), tools=[])
        rebuilt = get_tools_with_extensions()
        runtime.rebind_resources(tools=rebuilt)
        assert [t.name for t in runtime.tools] == [t.name for t in rebuilt]

"""``/reload`` — re-apply config, extensions, agents, tools, and context in place.

Modelled on pi-mono's ``AgentSession.reload()``, which is an explicit command
rather than a background watcher. Both halves of that choice matter here:

* **Explicit beats watched.** A file watcher has to guess whether a write is
  finished, has to debounce, and reloads on every keystroke-save from an editor
  that saves in bursts. A command fires once, when the user says the code is
  ready, and its cost is bounded and visible.
* **The order is the safety property.** pi-mono emits ``session_shutdown``
  before touching anything, clears the extension cache, reloads settings,
  rebuilds the runtime with the *previous* flag values, then re-emits
  ``session_start``. Extensions get a chance to release whatever they cached,
  and a reload never silently resets a choice the user made.

The one thing pi-mono does not have to handle: a Python extension's
submodules. Its loader re-executes the entry module every time, so an edited
entry file is picked up, but a package extension's submodules stay cached in
``sys.modules`` forever. That is handled explicitly below.
"""

from __future__ import annotations

import contextlib
import importlib
import sys
from dataclasses import dataclass

from vtx.ai.agent.tools import get_tools_with_extensions
from vtx.ai.config import config, reload_config
from vtx.tui.chat import ChatLog
from vtx.tui.commands.base import CommandSupport
from vtx.tui.widgets import InfoBar

#: Prefix the extension loader stamps on every module it imports.
_EXTENSION_MODULE_PREFIX = "vtx_ext_"


@dataclass(frozen=True)
class _Snapshot:
    """Reloadable state, captured before and after so the report can be specific."""

    commands: int
    agents: int
    skills: int
    tools: list[str]
    active_agent: str | None
    mode: str


class ReloadCommands(CommandSupport):
    """``/reload`` and its surface-specific refreshes."""

    def _handle_reload_command(self, args: str) -> None:
        chat = self.query_one("#chat-log", ChatLog)

        # A reload swaps the tool set and the event bus. Doing that underneath a
        # live agent loop would leave the in-flight turn holding tools that are
        # no longer the ones the next turn will advertise, and extension hooks
        # firing against a bus nobody holds a reference to. Refuse instead of
        # corrupting the run; the user can queue it or press Esc first.
        if getattr(self, "_is_running", False):
            chat.add_info_message(
                "Cannot reload while a turn is running. Press Esc to interrupt, "
                "or wait for it to finish.",
                warning=True,
            )
            return

        from vtx.ai.agent.extensions import SESSION_SHUTDOWN, load_for_runtime

        before = self._reload_snapshot()

        # 1. Tell the outgoing extensions to let go, exactly as pi-mono does
        #    before clearing its cache. This is their only chance to release
        #    sockets, subprocesses, and caches keyed to the old module objects.
        old_bus = getattr(self._loaded_extensions, "bus", None)
        if old_bus is not None and old_bus.handler_count(SESSION_SHUTDOWN):
            old_bus.emit_sync(
                SESSION_SHUTDOWN,
                cwd=self._cwd,
                session_id=self._session.id if self._session else "",
                reason="reload",
            )

        # 2. Drop the imported extension modules. load_extension() re-executes
        #    the entry module unconditionally, so an edited entry file would
        #    reload on its own — but a *package* extension imports its
        #    submodules by name, and those hit sys.modules and stay stale
        #    forever. Removing them, plus invalidating the import caches, is
        #    what makes a new or renamed submodule visible at all.
        self._invalidate_extension_modules()

        # 3. Config first: extension and agent discovery both read it, so
        #    reloading them against the old config would resolve old paths.
        try:
            reload_config()
        except Exception as exc:
            chat.add_info_message(f"config reload failed: {exc}", error=True)

        # 4. Extensions. `fresh=True` additionally drops each package
        #    extension's __pycache__, so a submodule edit of the same byte
        #    length is not served from stale bytecode.
        loaded = load_for_runtime(
            cwd=self._cwd, extra_paths=[*config.extensions], auto_discover=True, fresh=True
        )
        for err in loaded.errors:
            chat.add_info_message(f"extension: {err}", error=True)
        self._loaded_extensions = loaded

        # 5. Agents. Re-register their handlers on the *new* bus, since the one
        #    they registered on in step 1 is about to be unreachable.
        from vtx.ai.agent.agents import load_all_agents

        agent_loaded, agent_errors = load_all_agents(
            cwd=self._cwd,
            configured=list(config.agents.files),
            on_event=lambda event, handler: (
                loaded.bus.on(event, handler) if loaded.bus is not None else None
            ),
        )
        for err in agent_errors:
            chat.add_info_message(f"agent: {err}", error=True)

        # 6. Keep the user's active profile if it still resolves. Silently
        #    dropping to no agent would change the tool set under them.
        previous_name = before.active_agent
        self._runtime.agent_registry.agents = agent_loaded
        self._runtime.agent_registry.errors = agent_errors
        if previous_name and self._runtime.agent_registry.set_active(previous_name) is None:
            chat.add_info_message(
                f"active agent {previous_name!r} no longer resolves; continuing without it.",
                warning=True,
            )
        active = self._runtime.agent_registry.active

        # 7. Rebuild the tool set from the new extension and agent inventory.
        ext_tools = list(loaded.list_extension_tools())
        if active is not None:
            ext_tools.extend(active.local_tools.values())
            ext_tools.extend(loaded.local_tools_for(active.definition.name))
        tools = (
            get_tools_with_extensions(None, ext_tools)
            if ext_tools
            else get_tools_with_extensions()
        )
        self._tools = tools

        # 8. Rebind, then refresh context and the system prompt in one step.
        self._runtime.rebind_resources(
            tools=tools,
            extensions=loaded.bus,
            loaded_extensions=loaded,
            agent_registry=self._runtime.agent_registry,
            active_agent=active,
        )

        # 9. The / list is built from skills and extension commands, both of
        #    which just changed.
        self._sync_slash_commands()

        after = self._reload_snapshot()

        # 10. Extensions re-initialize against the new module objects.
        if loaded.bus is not None and loaded.bus.handler_count("session_start"):
            loaded.bus.emit_sync(
                "session_start",
                cwd=self._cwd,
                session_id=self._session.id if self._session else "",
                reason="reload",
            )

        self._report_reload(before, after)
        self._refresh_reload_dependent_ui()

    def _reload_snapshot(self) -> _Snapshot:
        """Cheap pre/post comparison so the report can say what actually changed."""
        try:
            skills = self._runtime.context.skills if self._runtime.context else []
        except Exception:
            skills = []
        active = self._runtime.agent_registry.active
        loaded = getattr(self, "_loaded_extensions", None)
        return _Snapshot(
            commands=len(getattr(loaded, "all_commands", {})),
            agents=len(self._runtime.agent_registry.agents),
            skills=len(skills),
            tools=sorted(t.name for t in self._tools),
            active_agent=active.definition.name if active else None,
            mode=config.mode,
        )

    def _invalidate_extension_modules(self) -> None:
        """Forget imported extension modules so edited code is actually re-read."""
        importlib.invalidate_caches()
        for name in [n for n in sys.modules if n.startswith(_EXTENSION_MODULE_PREFIX)]:
            # Drop the entry module and any submodule the package pulled in.
            # Removing the parent first is harmless: nothing re-imports it
            # within this call.
            sys.modules.pop(name, None)

    def _report_reload(self, before: _Snapshot, after: _Snapshot) -> None:
        chat = self.query_one("#chat-log", ChatLog)
        changes: list[str] = []
        if before.tools != after.tools:
            added = sorted(set(after.tools) - set(before.tools))
            removed = sorted(set(before.tools) - set(after.tools))
            if added:
                changes.append(f"+{', '.join(added)}")
            if removed:
                changes.append(f"-{', '.join(removed)}")
        if before.skills != after.skills:
            changes.append(f"skills {after.skills - before.skills:+d}")
        if before.agents != after.agents:
            changes.append(f"agents {after.agents - before.agents:+d}")
        if before.commands != after.commands:
            changes.append(f"commands {after.commands - before.commands:+d}")
        if before.mode != after.mode:
            changes.append(f"mode {before.mode} -> {after.mode}")

        summary = "; ".join(changes) if changes else "no visible changes"
        chat.add_info_message(f"Reloaded config, extensions, agents, tools, skills. ({summary})")
        # Theme is the one thing a reload genuinely cannot do: Textual compiles
        # the stylesheet at mount, so a new theme needs the process back.
        chat.add_info_message(
            "Themes and terminal-level settings still need a restart.", warning=True
        )

    def _refresh_reload_dependent_ui(self) -> None:
        """Re-render the bars that show config-derived state."""
        try:
            infobar = self.query_one(InfoBar)
        except Exception:
            return
        infobar.set_permission_mode(config.permissions.mode)
        with contextlib.suppress(Exception):
            infobar.refresh(layout=True)


__all__ = ["ReloadCommands"]

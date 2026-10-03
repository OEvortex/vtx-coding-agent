"""``/reload`` — re-apply config, extensions, agents, tools, and context in place.

An explicit command rather than a background watcher. Both halves of that
choice matter here:

* **Explicit beats watched.** A file watcher has to guess whether a write is
  finished, has to debounce, and reloads on every keystroke-save from an editor
  that saves in bursts. A command fires once, when the user says the code is
  ready, and its cost is bounded and visible.
* **The order is the safety property.** ``session_shutdown`` is emitted before
  touching anything, the extension cache is cleared, settings are reloaded, the
  runtime is rebuilt with the *previous* flag values, then ``session_start`` is
  re-emitted. Extensions get a chance to release whatever they cached, and a
  reload never silently resets a choice the user made.

The one part that needs handling beyond the obvious: a Python extension's
submodules. The loader re-executes the entry module every time, so an edited
entry file is picked up, but a package extension's submodules stay cached in
``sys.modules`` forever. That is handled explicitly below.
"""

from __future__ import annotations

import contextlib
import importlib
import sys
from dataclasses import dataclass

from vtx.agent.tools import get_tools_with_extensions
from vtx.coding_agent.tui.chat import ChatLog
from vtx.coding_agent.tui.commands.base import CommandSupport
from vtx.coding_agent.tui.widgets import InfoBar
from vtx.core.config import config, reload_config

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
    settings: dict[str, str]


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

        from vtx.agent.extensions import SESSION_SHUTDOWN, load_for_runtime

        before = self._reload_snapshot()

        # 1. Tell the outgoing extensions to let go, before clearing the cache. This
        #    is their only chance to release sockets, subprocesses, and caches
        #    keyed to the old module objects.
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
        from vtx.agent.agents import load_all_agents

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

        # 8. Rebind, then refresh context and the system prompt in one step, so a
        #    tool edit in config.yml switches the live surface over rather than
        #    only changing what the prompt claims.
        self._runtime.rebind_resources(
            tools=tools,
            extensions=loaded.bus,
            loaded_extensions=loaded,
            agent_registry=self._runtime.agent_registry,
            active_agent=active,
        )

        # 8b. `codemode` snapshots the tool set to build its sandbox, so it has
        #     to be told. Without this a newly added tool is invisible to scripts
        #     until the session ends, which reads as the reload not working.
        for tool in self._runtime.tools:
            refresh = getattr(tool, "refresh", None)
            if callable(refresh):
                refresh()

        # 9. The / list is built from skills and extension commands, both of
        #    which just changed.
        self._sync_slash_commands()

        # 9b. MCP servers. mcp.json is not part of config.yml, so /reload is
        #     the only thing that picks up an edit to it. Run as a worker
        #     rather than inline: connecting a server can take seconds, and
        #     /reload has already swapped everything else. Reloading closes the
        #     old connections first, which is what stops a removed server's
        #     child process from being orphaned.
        self.run_worker(self._reload_mcp(chat), exclusive=False)

        # 10. Push the settings that live outside the config object.
        applied_settings = self._apply_reloaded_settings(before.settings)

        after = self._reload_snapshot()

        # 11. Extensions re-initialize against the new module objects.
        if loaded.bus is not None and loaded.bus.handler_count("session_start"):
            loaded.bus.emit_sync(
                "session_start",
                cwd=self._cwd,
                session_id=self._session.id if self._session else "",
                reason="reload",
            )

        self._report_reload(before, after, applied_settings)

    async def _reload_mcp(self, chat: ChatLog) -> None:
        """Re-read ``mcp.json`` and reconnect, reporting the outcome."""
        try:
            tools = await self._runtime.reload_mcp()
        except Exception as exc:
            chat.add_info_message(f"MCP reload failed: {exc}", error=True)
            return
        if not tools:
            chat.add_info_message("MCP: no tools from any server.")
            return
        servers = ", ".join(
            s.name
            for s in self._runtime.ensure_mcp_manager().statuses()
            if s.state == "connected" and s.tool_count
        )
        chat.add_info_message(f"MCP: {len(tools)} tool(s) from {servers or 'no server'}.")

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
            settings=self._settings_snapshot(),
        )

    @staticmethod
    def _settings_snapshot() -> dict[str, str]:
        """The settings whose effect is not automatic on re-read.

        Most config is read at the moment it is used — the tool-result budget,
        the compaction threshold, ``colored_tool_badge``, ``thinking_lines`` — so
        reloading the file is enough for those. These are the ones something
        has to actively push at a live widget or a live tool set, and they are
        what the before/after diff reports on.
        """
        with contextlib.suppress(Exception):
            return {
                "theme": config.ui.theme,
                "permissions": config.permissions.mode,
                "thinking_lines": str(config.ui.thinking_lines),
                "colored_badge": str(config.ui.colored_tool_badge).lower(),
                "notifications": "on" if config.notifications.enabled else "off",
                "ponytail": str(config.llm.system_prompt.ponytail).lower(),
                "git_context": str(config.llm.system_prompt.git_context).lower(),
            }
        return {}

    def _apply_reloaded_settings(self, before: dict[str, str]) -> list[str]:
        """Push changed settings into the live UI. Returns what was applied.

        ``reload_config()`` has already updated the in-memory config, so the
        only thing left is the part that lives outside it: a stylesheet Textual
        compiled at mount, and a footer label.
        """
        applied: list[str] = []
        after = self._settings_snapshot()

        if before.get("theme") != after.get("theme") and "theme" in after:
            with contextlib.suppress(Exception):
                # get_styles() reads config.ui.colors, which UIConfig derives
                # from the theme, so this rebuilds the sheet for the new theme.
                self._apply_theme(after["theme"])
                applied.append(f"theme -> {after['theme']}")

        if before.get("permissions") != after.get("permissions") and "permissions" in after:
            with contextlib.suppress(Exception):
                infobar = self.query_one("#compact-footer", InfoBar)
                infobar.set_permission_mode(after["permissions"])
                applied.append(f"permissions -> {after['permissions']}")

        if before.get("thinking_lines") != after.get("thinking_lines"):
            applied.append(f"thinking lines -> {after.get('thinking_lines')}")
        if before.get("colored_badge") != after.get("colored_badge"):
            applied.append(f"tool badge -> {after.get('colored_badge')}")
        if before.get("notifications") != after.get("notifications"):
            applied.append(f"notifications -> {after.get('notifications')}")
        if before.get("ponytail") != after.get("ponytail"):
            applied.append(f"ponytail -> {after.get('ponytail')}")
        if before.get("git_context") != after.get("git_context"):
            applied.append(f"git context -> {after.get('git_context')}")
        return applied

    def _invalidate_extension_modules(self) -> None:
        """Forget imported extension modules so edited code is actually re-read."""
        importlib.invalidate_caches()
        for name in [n for n in sys.modules if n.startswith(_EXTENSION_MODULE_PREFIX)]:
            # Drop the entry module and any submodule the package pulled in.
            # Removing the parent first is harmless: nothing re-imports it
            # within this call.
            sys.modules.pop(name, None)

    def _report_reload(
        self, before: _Snapshot, after: _Snapshot, applied_settings: list[str]
    ) -> None:
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

        summary = "; ".join(changes) if changes else "no visible changes"
        chat.add_info_message(f"Reloaded config, extensions, agents, tools, skills. ({summary})")
        # Name the settings that were pushed at live widgets, so a theme or
        # permission change in config.yml is visibly confirmed rather than
        # silently assumed.
        if applied_settings:
            chat.add_info_message("Applied settings: " + ", ".join(applied_settings))
        else:
            chat.add_info_message("Settings unchanged.")


__all__ = ["ReloadCommands"]

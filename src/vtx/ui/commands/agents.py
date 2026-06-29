"""``/agent`` slash command — list, activate, reload, or deactivate agents.

Mirrors the structure of the other commands mixins in this package. The
TUI's Shift+Tab binding in :mod:`vtx.ui.app` calls into this mixin to
cycle through the registry.
"""

from __future__ import annotations

from vtx import config

from ..chat import ChatLog
from ..floating_list import FloatingList, ListItem
from ..widgets import InfoBar
from .base import CommandSupport


class AgentCommands(CommandSupport):
    """``/agent [list|current|reload|off|<name>]`` and Shift+Tab cycling."""

    def _show_agents_list(self) -> None:
        chat = self.query_one("#chat-log", ChatLog)
        rows = self._runtime.agent_registry.describe()
        if not rows:
            chat.add_info_message(
                "No agents found. Create one at .vtx/agent/<name>.py or "
                "~/.vtx/agent/<name>.py (see docs)."
            )
            return
        active_name = (
            self._runtime.active_agent.definition.name if self._runtime.active_agent else None
        )
        chat.add_agent_details(rows=rows, active=active_name)

    def _show_agent_current(self) -> None:
        chat = self.query_one("#chat-log", ChatLog)
        active = self._runtime.active_agent
        if active is None:
            chat.add_info_message("No active agent (default session profile).")
        else:
            chat.add_info_message(
                f"Active agent: {active.definition.name} — {active.definition.description}"
            )

    def _reload_agents(self) -> None:
        from ...agents import load_all_agents

        chat = self.query_one("#chat-log", ChatLog)
        loaded, errors = load_all_agents(cwd=self._cwd, configured=list(config.agents.files))
        # Replace the registry contents; preserve the current active selection
        # if its name still resolves.
        previous = self._runtime.active_agent
        previous_name = previous.definition.name if previous else None
        self._runtime.agent_registry.agents = loaded
        self._runtime.agent_registry.errors = errors
        # Re-apply the previous selection.
        if previous_name is not None:
            self._runtime.agent_registry.set_active(previous_name)
        # Re-apply the tool/command set.
        self._runtime._apply_active_agent_to_runtime()
        if errors:
            for err in errors:
                chat.add_info_message(f"agent reload error: {err}", error=True)
        chat.add_info_message(f"Reloaded {len(loaded)} agent(s).")

    def _set_active_agent(self, name: str) -> None:
        chat = self.query_one("#chat-log", ChatLog)
        # ``None`` means "deactivate" (back to default).
        target: str | None = None if name.lower() in ("off", "none", "default") else name
        new = self._runtime.set_active_agent(target)
        if new is None and target is not None:
            chat.add_info_message(f"Agent {name!r} not found.", error=True)
            return
        self._sync_runtime_state()
        info_bar = self.query_one("#info-bar", InfoBar)
        info_bar.set_agent(new.definition.name if new else "")

    def _pick_agent(self) -> None:
        """Open the agent picker (floating list)."""
        chat = self.query_one("#chat-log", ChatLog)
        rows = self._runtime.agent_registry.describe()
        if not rows:
            chat.add_info_message("No agents to pick.")
            return
        active = self._runtime.active_agent
        active_name = active.definition.name if active else None
        items: list[ListItem] = []
        # Always offer "no agent" first.
        items.append(
            ListItem(value=None, label="(no agent)", description="default session profile")
        )
        for r in rows:
            label = f"{r['name']}"
            if r.get("icon"):
                label = f"{r['icon']}  {label}"
            if r["name"] == active_name:
                label = f"● {label}"
            items.append(
                ListItem(value=r["name"], label=label, description=r.get("description") or "")
            )
        accent = config.ui.colors.accent
        floating = self.query_one("#completion-list", FloatingList)
        floating.show(
            items,
            title="Switch agent",
            accent_color=accent,
            on_select=lambda item: self._on_agent_pick(item.value if item else None),
        )

    def _on_agent_pick(self, name: str | None) -> None:
        chat = self.query_one("#chat-log", ChatLog)
        if name is None and not self._runtime.agent_registry.agents:
            return
        new = self._runtime.set_active_agent(name)
        if new is None and name is not None:
            chat.add_info_message(f"Agent {name!r} not found.", error=True)
            return
        self._sync_runtime_state()
        info_bar = self.query_one("#info-bar", InfoBar)
        info_bar.set_agent(new.definition.name if new else "")

    def action_cycle_agent(self) -> None:
        """Shift+Tab handler. Cycles to the next agent (or none)."""
        new = self._runtime.cycle_active_agent()
        self._sync_runtime_state()
        try:
            info_bar = self.query_one("#info-bar", InfoBar)
            info_bar.set_agent(new.definition.name if new else "")
        except Exception:
            pass

    def action_cycle_tool_group(self) -> None:
        """Alt+Ctrl+G handler. Cycles to the next tool group for the active agent."""
        new_group = self._runtime.cycle_active_tool_group()
        self._sync_runtime_state()
        if new_group is None:
            return
        chat = self.query_one("#chat-log", ChatLog)
        active = self._runtime.active_agent
        agent_name = active.definition.name if active else ""
        chat.add_info_message(f"Tool group: {new_group}  ({agent_name})")

    def _handle_agent_command(self, args: str) -> None:
        """``/agent [list|current|reload|off|<name>]`` — no args opens the picker."""
        sub = args.strip()
        if not sub:
            self._pick_agent()
            return
        lowered = sub.lower()
        if lowered in ("list", "ls"):
            self._show_agents_list()
        elif lowered in ("current", "status"):
            self._show_agent_current()
        elif lowered == "reload":
            self._reload_agents()
        elif lowered in ("off", "none", "default"):
            self._set_active_agent("off")
        else:
            self._set_active_agent(sub)


__all__ = ["AgentCommands"]

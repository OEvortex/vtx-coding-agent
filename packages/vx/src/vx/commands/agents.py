"""``/agent`` and its ``/switch`` alias."""

from __future__ import annotations

import contextlib

from ctx.core.config import config

from vx.commands.base import CommandSupport
from vx.tui import footer


class AgentCommands(CommandSupport):
    def _agent_picker_items(self) -> list[footer.MenuItem]:
        rt = self.runtime
        if rt is None:
            return []
        rows = rt.agent_registry.describe()
        active = rt.active_agent
        active_name = active.definition.name if active else None
        items = [
            footer.MenuItem(
                label="● (no agent)" if active_name is None else "(no agent)",
                detail="default session profile",
                value="",
            )
        ]
        for row in rows:
            name = str(row.get("name") or "")
            icon = str(row.get("icon") or "")
            label = f"{icon}  {name}" if icon else name
            if name == active_name:
                label = f"● {label}"
            items.append(
                footer.MenuItem(
                    label=label,
                    detail=str(row.get("description") or ""),
                    metadata=" · ".join(sorted(row.get("tools") or []))[:48],
                    value=name,
                )
            )
        return items

    def _handle_agent_command(self, args: str) -> None:
        sub = args.strip()
        if not sub:
            items = self._agent_picker_items()
            if len(items) <= 1:
                self._warn(
                    "no agents found. add one at .ctx/agent/<name>.py or ~/.ctx/agent/<name>.py"
                )
                return
            self._open_menu(
                items,
                noun="Agents",
                on_select=lambda item: self._set_active_agent(item.value or ""),
            )
            return
        lowered = sub.lower()
        if lowered in ("list", "ls"):
            self._show_agents_list()
        elif lowered in ("current", "status"):
            self._show_agent_current()
        elif lowered == "reload":
            self._reload_agents()
        else:
            self._set_active_agent("" if lowered in ("off", "none", "default") else sub)

    def _show_agents_list(self) -> None:
        rt = self.runtime
        if rt is None:
            return
        rows = rt.agent_registry.describe()
        if not rows:
            self._warn(
                "no agents found. add one at .ctx/agent/<name>.py or ~/.ctx/agent/<name>.py"
            )
            return
        active = rt.active_agent
        active_name = active.definition.name if active else None
        sections = [
            (
                f"{len(rows)} agents",
                [
                    (
                        f"{'● ' if row.get('name') == active_name else '  '}{row.get('name')}",
                        str(row.get("description") or ""),
                    )
                    for row in rows
                ],
            )
        ]
        self._report("Agents", sections, detail="enter on /agent to switch")

    def _show_agent_current(self) -> None:
        rt = self.runtime
        active = rt.active_agent if rt else None
        if active is None:
            self._notice("no active agent (default session profile)", topic="agent")
            return
        self._notice(f"{active.definition.name} — {active.definition.description}", topic="agent")

    def _reload_agents(self) -> None:
        from ctx.agent.agents import load_all_agents

        rt = self.runtime
        if rt is None:
            return
        previous = rt.active_agent
        previous_name = previous.definition.name if previous else None
        loaded, errors = load_all_agents(cwd=self.cwd, configured=list(config.agents.files))
        rt.agent_registry.agents = loaded
        rt.agent_registry.errors = errors
        if previous_name is not None:
            rt.agent_registry.set_active(previous_name)
        with contextlib.suppress(Exception):
            rt._apply_active_agent_to_runtime()
        for error in errors:
            self._fail(f"agent reload error: {error}")
        self._ok(f"reloaded {len(loaded)} agent(s)", topic="agent")

    def _set_active_agent(self, name: str) -> None:
        rt = self.runtime
        if rt is None:
            return
        target = None if name.lower() in ("", "off", "none", "default") else name
        new = rt.set_active_agent(target)
        if new is None and target is not None:
            self._fail(f"agent {name!r} not found")
            return
        self._ok(f"agent: {new.definition.name}" if new else "agent deactivated", topic="agent")

    def _cycle_agent(self) -> None:
        rt = self.runtime
        if rt is None:
            return
        new = rt.cycle_active_agent()
        self._ok(f"agent: {new.definition.name}" if new else "agent deactivated", topic="agent")

    def _handle_switch_command(self, args: str) -> None:
        self._handle_agent_command(args.strip())

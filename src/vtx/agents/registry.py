"""Registry of loaded agents + the currently active agent.

The registry is the runtime-facing view of all available agents. The
:class:`ConversationRuntime` holds one of these.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from pathlib import Path

from .api import LoadedAgent


@dataclass
class AgentRegistry:
    """All loaded agents, plus the currently active one.

    The active agent is the one the user is currently "in" (via Shift+Tab
    or ``/agent <name>``). ``None`` means no agent is active — the
    runtime falls back to its session-default behavior.
    """

    agents: list[LoadedAgent] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    _active: LoadedAgent | None = None
    # Optional callback fired whenever the active agent changes. The
    # runtime passes a function that emits ``agent_changed`` on the
    # extensions event bus.
    _on_change: list = field(default_factory=list)

    def by_name(self, name: str) -> LoadedAgent | None:
        for a in self.agents:
            if a.definition.name == name:
                return a
        return None

    @property
    def names(self) -> list[str]:
        builtins = sorted(a.definition.name for a in self.agents if str(a.path) == "<builtin>")
        users = sorted(a.definition.name for a in self.agents if str(a.path) != "<builtin>")
        return builtins + users

    @property
    def active(self) -> LoadedAgent | None:
        return self._active

    @property
    def tool_group_cycle(self) -> list[str | None]:
        """Return the cycle of tool-group keys for the active agent.

        ``[None, *group_keys]`` so callers can cycle the same way agent
        cycling works.
        """
        if self._active is None or not self._active.definition.tool_groups:
            return [None]
        return [None, *self._active.definition.tool_groups.keys()]

    @property
    def active_tool_group(self) -> str | None:
        """The currently selected ``active_tool_group`` for the active agent."""
        if self._active is None:
            return None
        return self._active.definition.active_tool_group

    def set_on_change(self, callback) -> None:
        """Register a callable fired with the new active agent (or None)."""
        self._on_change.append(callback)

    def set_active(self, name: str | None) -> LoadedAgent | None:
        """Set the active agent by name (or ``None`` to clear).

        Returns the resolved :class:`LoadedAgent` (or ``None``). If the
        name is unknown, returns ``None`` and leaves the active agent
        unchanged.
        """
        if name is None:
            new = None
        else:
            new = self.by_name(name)
            if new is None:
                return None
        if new is self._active:
            return new
        self._active = new
        for cb in self._on_change:
            with contextlib.suppress(Exception):
                cb(new)
        return new

    def cycle(self) -> LoadedAgent | None:
        """Cycle through ``[None, *names]`` alphabetically and return the new active.

        With no agents loaded, this is a no-op.
        """
        cycle = [None, *(self.by_name(n) for n in self.names)]
        if not self.agents:
            return None
        try:
            idx = cycle.index(self._active)
        except ValueError:
            idx = -1
        new = cycle[(idx + 1) % len(cycle)]
        self._active = new
        for cb in self._on_change:
            with contextlib.suppress(Exception):
                cb(new)
        return new

    def cycle_tool_group(self) -> str | None:
        """Cycle through ``tool_group_cycle`` for the active agent.

        Writes the new group key back to ``active_tool_group`` on the
        agent's :class:`AgentDef`. Returns the new value (or ``None``).
        """
        cycle = self.tool_group_cycle
        if not cycle:
            return None
        current = self.active_tool_group
        try:
            idx = cycle.index(current)
        except ValueError:
            idx = -1
        new_key = cycle[(idx + 1) % len(cycle)]
        if self._active is not None:
            self._active.definition.active_tool_group = new_key
        return new_key

    def describe(self) -> list[dict]:
        """For ``/agent list`` and headless ``--list-agents``."""
        rows: list[dict] = []
        for name in self.names:
            a = self.by_name(name)
            if a is None:
                continue
            rows.append(
                {
                    "name": a.definition.name,
                    "description": a.definition.description,
                    "icon": a.definition.icon,
                    "path": str(a.path),
                    "tools": sorted(a.local_tools.keys()),
                    "commands": sorted(a.local_commands.keys()),
                    "extensions": list(a.definition.extensions),
                    "active": a is self._active,
                }
            )
        return rows


__all__ = ["AgentRegistry"]


def _collect_extensions_for(agent: LoadedAgent, all_extensions) -> list:
    """Helper used by the runtime to merge an agent's declared extensions
    with the loaded extension list. Not exported; kept here to avoid a
    circular import with :mod:`vtx.extensions`."""
    if not agent.definition.extensions:
        return []
    by_path: dict[Path, object] = {e.path: e for e in all_extensions}
    out = []
    for path_str in agent.definition.extensions:
        p = Path(path_str).expanduser()
        try:
            resolved = p.resolve()
        except OSError:
            continue
        ext = by_path.get(resolved)
        if ext is not None:
            out.append(ext)
    return out

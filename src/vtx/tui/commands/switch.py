"""``/switch`` slash command — switch to a named agent.

Thin wrapper around the existing ``/agent <name>`` handler so users can
type either ``/agent crs`` or ``/switch crs`` to activate the CRS agent.
"""

from __future__ import annotations

from vtx.tui.commands.agents import AgentCommands


class SwitchCommands(AgentCommands):
    """``/switch <name>`` — alias for ``/agent <name>``."""

    def _handle_switch_command(self, args: str) -> None:
        """Delegate to the existing agent handler on self."""
        self._handle_agent_command(args.strip())


__all__ = ["SwitchCommands"]

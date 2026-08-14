"""``/switch`` slash command — switch to a named agent.

Thin wrapper around the existing ``/agent <name>`` handler so users can
type either ``/agent crs`` or ``/switch crs`` to activate the CRS agent.
"""

from __future__ import annotations

from .base import CommandSupport


class SwitchCommands(CommandSupport):
    """``/switch <name>`` — alias for ``/agent <name>``."""

    def _handle_switch_command(self, args: str) -> None:
        """Delegate to the existing agent handler on self."""
        # ``CommandsMixin`` already inherits from ``AgentCommands``, so
        # ``self`` has ``_handle_agent_command`` available at runtime.
        self._handle_agent_command(args.strip())


__all__ = ["SwitchCommands"]

"""vx's ctx AgentHook bridge, kept out of the boot path.

``ctx.agent.hooks`` drags ``ctx.protocol`` (and aiohttp) in with it, and the
approval hook is not needed until the first turn runs. ``vx.permissions`` stays
import-free of ctx so the shell can paint and read keys before either loads.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ctx.agent.hooks.agent_hook import AgentHook

if TYPE_CHECKING:
    from vx.permissions import PermissionRuntime


class VxPermissionHook(AgentHook):
    """Bridges vx's PermissionRuntime into ctx's AgentHook system.

    Implements ``before_execute_tools`` to check each tool call against the
    current permission mode: ask mode routes mutating tools through the
    existing ToolApprovalEvent flow, auto mode lets everything pass.
    """

    def __init__(self, perm_runtime: PermissionRuntime) -> None:
        super().__init__()
        self.perm_runtime = perm_runtime

    async def before_execute_tools(self, context) -> None:
        """Check each pending tool call against the permission mode.

        This fires before the engine executes any tools in the iteration. The
        approval gate itself lives in the turn loop, so the hook records a
        decision rather than blocking on one.
        """
        for tc in context.tool_calls:
            tool_name = getattr(tc, "name", "")
            target = ""
            args = getattr(tc, "arguments", None)
            if isinstance(args, dict):
                for key in ("path", "file_path", "filename", "command", "cmd"):
                    val = args.get(key)
                    if isinstance(val, str) and val.strip():
                        target = val.strip()
                        break
            self.perm_runtime.check(tool_name, target)


__all__ = ["VxPermissionHook"]

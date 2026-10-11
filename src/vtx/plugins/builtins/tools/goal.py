"""The ``goal`` tool row."""

from __future__ import annotations

from vtx.agent.goal.tools import GoalTool
from vtx.plugins.builtins.tools._register import mount_tool
from vtx.plugins.plugin import plugin


@plugin(name="tool-goal", inject=("tools",))
def apply(ctx, config):
    """Parent-only for the same reason as ``delegate_subagent``: a goal is a
    whole-session objective, and a sub-agent's objective belongs to its parent."""
    mount_tool(ctx, GoalTool(), is_default=True, parent_only=True)


PLUGIN = apply

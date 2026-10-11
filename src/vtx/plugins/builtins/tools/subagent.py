"""The ``delegate_subagent`` tool row."""

from __future__ import annotations

from vtx.agent.tools.task import TaskTool
from vtx.plugins.builtins.tools._register import mount_tool
from vtx.plugins.plugin import plugin


@plugin(name="tool-subagent", inject=("tools",))
def apply(ctx, config):
    """Parent-only: a sub-agent delegating to another sub-agent is how you get
    unbounded recursion, so the registry keeps the name out of child tool sets."""
    mount_tool(ctx, TaskTool(), is_default=True, parent_only=True)


PLUGIN = apply

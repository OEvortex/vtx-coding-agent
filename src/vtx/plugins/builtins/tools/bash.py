"""The bash tool row."""

from __future__ import annotations

from vtx.coding_agent.tools.bash import BashTool
from vtx.plugins.builtins.tools._register import mount_tool
from vtx.plugins.plugin import plugin


@plugin(name="tool-bash", inject=("tools",))
def apply(ctx, config):
    mount_tool(ctx, BashTool(), is_default=True, parent_only=False)


PLUGIN = apply

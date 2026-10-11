"""The write tool row."""

from __future__ import annotations

from vtx.coding_agent.tools.write import WriteTool
from vtx.plugins.builtins.tools._register import mount_tool
from vtx.plugins.plugin import plugin


@plugin(name="tool-write", inject=("tools",))
def apply(ctx, config):
    mount_tool(ctx, WriteTool(), is_default=True, parent_only=False)


PLUGIN = apply

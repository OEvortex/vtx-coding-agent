"""The ``read`` tool row."""

from __future__ import annotations

from vtx.coding_agent.tools.read import ReadTool
from vtx.plugins.builtins.tools._register import mount_tool
from vtx.plugins.plugin import plugin


@plugin(name="tool-read", inject=("tools",))
def apply(ctx, config):
    mount_tool(ctx, ReadTool(), is_default=True, parent_only=False)


PLUGIN = apply

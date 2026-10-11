"""The edit tool row."""

from __future__ import annotations

from vtx.coding_agent.tools.edit import EditTool
from vtx.plugins.builtins.tools._register import mount_tool
from vtx.plugins.plugin import plugin


@plugin(name="tool-edit", inject=("tools",))
def apply(ctx, config):
    mount_tool(ctx, EditTool(), is_default=True, parent_only=False)


PLUGIN = apply

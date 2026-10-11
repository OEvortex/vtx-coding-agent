"""The find tool row."""

from __future__ import annotations

from vtx.agent.tools.find import FindTool
from vtx.plugins.builtins.tools._register import mount_tool
from vtx.plugins.plugin import plugin


@plugin(name="tool-find", inject=("tools",))
def apply(ctx, config):
    mount_tool(ctx, FindTool(), is_default=True, parent_only=False)


PLUGIN = apply

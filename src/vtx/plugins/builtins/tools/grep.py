"""The grep tool row."""

from __future__ import annotations

from vtx.agent.tools.grep import GrepTool
from vtx.plugins.builtins.tools._register import mount_tool
from vtx.plugins.plugin import plugin


@plugin(name="tool-grep", inject=("tools",))
def apply(ctx, config):
    mount_tool(ctx, GrepTool(), is_default=False, parent_only=False)


PLUGIN = apply

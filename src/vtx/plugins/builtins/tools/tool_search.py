"""The harness's own ``tool_search`` tool row."""

from __future__ import annotations

from vtx.agent.tools.tool_search import ToolSearchTool
from vtx.plugins.builtins.tools._register import mount_tool
from vtx.plugins.plugin import plugin


@plugin(name="tool-search", inject=("tools",))
def apply(ctx, config):
    """Registered but not in the default set: the harness mounts it alongside
    ``codemode``, which is what makes deferred tools reachable."""
    mount_tool(ctx, ToolSearchTool(), is_default=True, parent_only=False)


PLUGIN = apply

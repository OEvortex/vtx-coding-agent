"""The ``web`` and ``web_search`` tool rows."""

from __future__ import annotations

from vtx.agent.tools.web import WebSearchTool, WebTool
from vtx.plugins.builtins.tools._register import mount_tool
from vtx.plugins.plugin import plugin


@plugin(name="tool-web", inject=("tools",))
def apply(ctx, config):
    mount_tool(ctx, WebTool(), is_default=True, parent_only=False)
    mount_tool(ctx, WebSearchTool(), is_default=False, parent_only=False)


PLUGIN = apply

"""``ctx.mcp`` — external servers and the tools they expose."""

from __future__ import annotations

from vtx.plugins.plugin import plugin
from vtx.plugins.services import MCPService


@plugin(name="mcp")
def apply(ctx, config):
    """Publish the MCP seam.

    Building the manager is deferred to :meth:`MCPService.connect`, which the
    host calls once it knows the working directory. Import-time connection
    would build a manager for a directory that may not be the one in use.
    """
    mcp = MCPService()
    ctx.effect(mcp.reset)
    ctx.provide("mcp", mcp)


PLUGIN = apply

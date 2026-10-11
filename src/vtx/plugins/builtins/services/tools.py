"""``ctx.tools`` — the scoped tool registry service."""

from __future__ import annotations

from vtx.plugins.plugin import plugin
from vtx.plugins.services import ToolService


@plugin(name="tools")
def apply(ctx, config):
    """Publish the tool registry.

    Mounts before every tool row, because they all inject ``tools``; a
    composition without it cannot mount a tool at all.
    """
    tools = ToolService()
    ctx.effect(tools.reset)
    ctx.provide("tools", tools)


PLUGIN = apply

"""``ctx.agents`` — agent profiles and the active selection."""

from __future__ import annotations

from vtx.plugins.plugin import plugin
from vtx.plugins.services import AgentService


@plugin(name="agents")
def apply(ctx, config):
    ctx.provide("agents", AgentService())


PLUGIN = apply

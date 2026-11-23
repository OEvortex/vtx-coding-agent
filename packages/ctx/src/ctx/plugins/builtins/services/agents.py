"""``ctx.agents`` — agent profiles and the active selection."""

from __future__ import annotations

from ctx.plugins.plugin import plugin
from ctx.plugins.services import AgentService


@plugin(name="agents")
def apply(ctx, config):
    ctx.provide("agents", AgentService())


PLUGIN = apply

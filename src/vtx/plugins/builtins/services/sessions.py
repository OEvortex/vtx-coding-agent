"""``ctx.sessions`` — the durable session log."""

from __future__ import annotations

from vtx.plugins.plugin import plugin
from vtx.plugins.services import SessionService


@plugin(name="sessions")
def apply(ctx, config):
    ctx.provide("sessions", SessionService())


PLUGIN = apply

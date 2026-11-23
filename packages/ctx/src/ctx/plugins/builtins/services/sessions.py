"""``ctx.sessions`` — the durable session log."""

from __future__ import annotations

from ctx.plugins.plugin import plugin
from ctx.plugins.services import SessionService


@plugin(name="sessions")
def apply(ctx, config):
    ctx.provide("sessions", SessionService())


PLUGIN = apply

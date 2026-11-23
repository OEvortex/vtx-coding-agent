"""``ctx.sandbox`` — confined script execution (the codemode worker)."""

from __future__ import annotations

from ctx.plugins.plugin import plugin
from ctx.plugins.services import SandboxService


@plugin(name="sandbox")
def apply(ctx, config):
    ctx.provide("sandbox", SandboxService())


PLUGIN = apply

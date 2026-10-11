"""``ctx.sandbox`` — confined script execution (the codemode worker)."""

from __future__ import annotations

from vtx.plugins.plugin import plugin
from vtx.plugins.services import SandboxService


@plugin(name="sandbox")
def apply(ctx, config):
    ctx.provide("sandbox", SandboxService())


PLUGIN = apply

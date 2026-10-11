"""``ctx.permissions`` — the mutating-tool gate."""

from __future__ import annotations

from vtx.plugins.plugin import plugin
from vtx.plugins.services import PermissionService


@plugin(name="permissions")
def apply(ctx, config):
    """Publish the permission gate.

    The base decision stays whatever the user configured; plugins add rules
    that can only tighten it, so no plugin can silently auto-approve a
    destructive call.
    """
    ctx.provide("permissions", PermissionService())


PLUGIN = apply

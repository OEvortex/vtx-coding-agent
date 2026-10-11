"""``ctx.extensions`` — the extension discovery service."""

from __future__ import annotations

from vtx.plugins.extensions import ExtensionService
from vtx.plugins.plugin import plugin


@plugin(name="extensions", inject=("tools",))
def apply(ctx, config):
    """Publish the discovery service.

    ``boot`` calls :meth:`ExtensionService.load` before this row mounts, and the
    rows it returns are appended to the same composition — an extension file is
    one row among the profile's, not a parallel system.
    """
    existing = ctx.get("extensions")
    if existing is not None:
        # ``boot`` created it up front so discovery could run before mount; keep
        # the object holding the record rather than replacing it with an empty one.
        return existing
    ctx.provide("extensions", ExtensionService())


PLUGIN = apply

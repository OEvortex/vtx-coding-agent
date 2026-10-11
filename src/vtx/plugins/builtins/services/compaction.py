"""``ctx.compaction`` — overflow detection and summarisation."""

from __future__ import annotations

from vtx.plugins.plugin import plugin
from vtx.plugins.services import CompactionService


@plugin(name="compaction")
def apply(ctx, config):
    ctx.provide("compaction", CompactionService())


PLUGIN = apply

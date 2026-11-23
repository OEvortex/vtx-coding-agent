"""``ctx.compaction`` — overflow detection and summarisation."""

from __future__ import annotations

from ctx.plugins.plugin import plugin
from ctx.plugins.services import CompactionService


@plugin(name="compaction")
def apply(ctx, config):
    ctx.provide("compaction", CompactionService())


PLUGIN = apply

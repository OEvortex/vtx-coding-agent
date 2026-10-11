"""``ctx.skills`` — the skill catalog a prompt indexes."""

from __future__ import annotations

from vtx.plugins.plugin import plugin
from vtx.plugins.services import SkillService


@plugin(name="skills")
def apply(ctx, config):
    ctx.provide("skills", SkillService())


PLUGIN = apply

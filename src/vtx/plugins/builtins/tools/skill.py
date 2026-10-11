"""The skill tool row."""

from __future__ import annotations

from vtx.coding_agent.tools.skill import SkillTool
from vtx.plugins.builtins.tools._register import mount_tool
from vtx.plugins.plugin import plugin


@plugin(name="tool-skill", inject=("tools",))
def apply(ctx, config):
    mount_tool(ctx, SkillTool(), is_default=True, parent_only=False)


PLUGIN = apply

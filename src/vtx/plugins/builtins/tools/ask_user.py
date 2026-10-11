"""The ``ask_user`` tool row."""

from __future__ import annotations

from vtx.agent.tools.ask_user import AskUserTool
from vtx.plugins.builtins.tools._register import mount_tool
from vtx.plugins.plugin import plugin


@plugin(name="tool-ask-user", inject=("tools",))
def apply(ctx, config):
    mount_tool(ctx, AskUserTool(), is_default=True, parent_only=True)


PLUGIN = apply

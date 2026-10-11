"""The ``codemode`` tool row."""

from __future__ import annotations

from vtx.agent.tools.codemode import CodemodeTool
from vtx.plugins.builtins.tools._register import mount_tool
from vtx.plugins.plugin import plugin


@plugin(name="tool-codemode", inject=("tools",))
def apply(ctx, config):
    """``codemode`` composes other tools rather than adding a capability of its
    own, but it is default-on: a tool the model has to be told about before it
    uses it is a tool it will not think to use."""
    mount_tool(ctx, CodemodeTool(), is_default=True)


PLUGIN = apply

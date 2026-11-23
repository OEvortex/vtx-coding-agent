"""Shared base for the coding agent's tool blocks.

Everything here subclasses the generic :class:`ctx.tui.blocks.ToolBlock`
shipped by ``term``. Tool-specific rendering lives in the sibling modules;
the base only gives the simple tools a stable class to point their
``ui_block`` at.
"""

from __future__ import annotations

from ctx.tui.blocks import ToolBlock  # ty: ignore[import-not-found]


class CodingToolBlock(ToolBlock):
    """Default block for tools whose result is a summary plus optional detail.

    The generic header, approval controls, live output, and detail/expand
    handling inherited from ``ToolBlock`` are all these tools need.
    """


class FindToolBlock(CodingToolBlock):
    """Block for ``find`` results."""


class GrepToolBlock(CodingToolBlock):
    """Block for ``grep`` results."""


class WebToolBlock(CodingToolBlock):
    """Block for ``web`` / ``web_search`` results."""


class SkillToolBlock(CodingToolBlock):
    """Block for ``skill`` results."""


class ToolSearchToolBlock(CodingToolBlock):
    """Block for ``tool_search`` results."""

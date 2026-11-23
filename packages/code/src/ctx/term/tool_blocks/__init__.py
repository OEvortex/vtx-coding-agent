"""Tool-specific chat blocks for the coding agent.

The generic building blocks live in ``term`` (``ctx.term.blocks``); this
package owns every block that knows what a specific tool's call or result
looks like. Each tool in ``ctx.code.tools`` points its ``ui_block``
at one of the classes here.
"""

from __future__ import annotations

from .ask_user import AskUserToolBlock
from .base import (
    CodingToolBlock,
    FindToolBlock,
    GrepToolBlock,
    SkillToolBlock,
    ToolSearchToolBlock,
    WebToolBlock,
)
from .bash import BashToolBlock
from .patch import ApplyPatchToolBlock
from .read import ReadToolBlock
from .subagent import TaskToolBlock

__all__ = [
    "ApplyPatchToolBlock",
    "AskUserToolBlock",
    "BashToolBlock",
    "CodingToolBlock",
    "FindToolBlock",
    "GrepToolBlock",
    "ReadToolBlock",
    "SkillToolBlock",
    "TaskToolBlock",
    "ToolSearchToolBlock",
    "WebToolBlock",
]

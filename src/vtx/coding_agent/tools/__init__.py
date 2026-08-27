"""Concrete built-in tools of the Vtx coding agent.

Registers concrete tools (:class:`ReadTool`, :class:`EditTool`, :class:`WriteTool`,
:class:`BashTool`, :class:`FindTool`, :class:`GrepTool`, :class:`SkillTool`) into the
central harness tool registry at :mod:`vtx.ai.agent.tools`.
"""

from __future__ import annotations

from vtx.ai.agent.tools import (
    BaseTool,
    get_tool_definitions,
    register_tool,
    set_default_tool_lookup,
)

from ..goal.tools import GoalTool
from .ask_user import AskUserTool
from .background import BackgroundTaskManager, BackgroundTaskRecord, get_manager, set_manager
from .bash import BashTool
from .edit import EditTool
from .find import FindTool
from .grep import GrepTool
from .read import ReadTool
from .skill import SkillTool
from .task import TaskTool
from .web import WebSearchTool, WebTool
from .write import WriteTool

__all__ = [
    "DEFAULT_TOOLS",
    "PARENT_ONLY_TOOLS",
    "AskUserTool",
    "BackgroundTaskManager",
    "BackgroundTaskRecord",
    "BashTool",
    "EditTool",
    "FindTool",
    "GrepTool",
    "ReadTool",
    "SkillTool",
    "TaskTool",
    "WebSearchTool",
    "WebTool",
    "WriteTool",
    "get_manager",
    "get_tool",
    "get_tool_definitions",
    "get_tools",
    "get_tools_with_extensions",
    "set_manager",
    "tools_by_name",
]

all_tools: list[BaseTool] = [
    ReadTool(),
    EditTool(),
    WriteTool(),
    BashTool(),
    FindTool(),
    GrepTool(),
    SkillTool(),
    WebTool(),
    AskUserTool(),
    TaskTool(),
    GoalTool(),
]

tools_by_name: dict[str, BaseTool] = {tool.name: tool for tool in all_tools}
all_tools_set: set[str] = {tool.name for tool in all_tools}
DEFAULT_TOOLS: list[str] = [
    "read",
    "edit",
    "write",
    "bash",
    "find",
    "skill",
    "web",
    "ask_user",
    "task",
    "goal",
]

PARENT_ONLY_TOOLS: frozenset[str] = frozenset({"task", "goal"})


def get_tools(names: list[str]) -> list[BaseTool]:
    return [tool for tool in all_tools if tool.name in names]


def get_tool(tool_name: str) -> BaseTool | None:
    return tools_by_name.get(tool_name)


def get_tools_with_extensions(
    default_names: list[str], extension_tools: list[BaseTool] | None = None
) -> list[BaseTool]:
    tools = get_tools(default_names)
    if extension_tools:
        for ext_tool in extension_tools:
            if ext_tool.name not in [t.name for t in tools]:
                tools.append(ext_tool)
    return tools


# Auto-register all coding-agent concrete tools into the harness registry
for _t in all_tools:
    register_tool(
        _t, is_default=(_t.name in DEFAULT_TOOLS), parent_only=(_t.name in PARENT_ONLY_TOOLS)
    )

set_default_tool_lookup(get_tool)

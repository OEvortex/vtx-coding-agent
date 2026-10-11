"""Compatibility surface for the Vtx coding agent's built-in tools.

The concrete tool implementations live in :mod:`vtx.agent.tools`; the plugin
rows in :mod:`vtx.plugins.builtins.tools` mount them into the scoped registry.
This module keeps the historical ``vtx.coding_agent.tools`` import path
working: importing it has the side effect of registering the built-in tools
into the harness registry, so the tools exist the moment you import them.
"""

from __future__ import annotations

from vtx.agent.background import (
    BackgroundTaskManager,
    BackgroundTaskRecord,
    get_manager,
    set_manager,
)
from vtx.agent.goal.tools import GoalTool
from vtx.agent.tools import (
    AskUserTool,
    BaseTool,
    BashTool,
    EditTool,
    FindTool,
    GrepTool,
    ReadTool,
    SkillTool,
    TaskTool,
    WebSearchTool,
    WebTool,
    WriteTool,
    get_tool_definitions,
    get_tools_with_extensions,
    register_tool,
    set_default_tool_lookup,
)

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
    WebSearchTool(),
    AskUserTool(),
    TaskTool(),
    GoalTool(),
]

tools_by_name: dict[str, BaseTool] = {tool.name: tool for tool in all_tools}
DEFAULT_TOOLS: list[str] = [
    "read",
    "edit",
    "write",
    "bash",
    "find",
    "skill",
    "web",
    "ask_user",
    "delegate_subagent",
    "goal",
]

PARENT_ONLY_TOOLS: frozenset[str] = frozenset({"delegate_subagent", "goal"})


def get_tools(names: list[str]) -> list[BaseTool]:
    return [tool for tool in all_tools if tool.name in names]


def get_tool(tool_name: str) -> BaseTool | None:
    return tools_by_name.get(tool_name)


# Auto-register all coding-agent concrete tools into the harness registry
for _t in all_tools:
    register_tool(
        _t, is_default=(_t.name in DEFAULT_TOOLS), parent_only=(_t.name in PARENT_ONLY_TOOLS)
    )

set_default_tool_lookup(get_tool)

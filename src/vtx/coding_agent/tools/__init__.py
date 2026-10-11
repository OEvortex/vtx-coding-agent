"""The Vtx coding agent's built-in tools.

Each tool module here owns both its implementation and its plugin row (the
``PLUGIN`` export), so all registration goes through the plugins system: the
base bundle in :mod:`vtx.plugins.builtins` addresses these rows as
``vtx.coding_agent.tools.<mod>:PLUGIN``, and mounting the composition is what
puts the tools in the registry. Importing this package registers nothing.

The module keeps the historical import path working: ``all_tools``,
``tools_by_name``, ``get_tools`` and :data:`DEFAULT_TOOLS` describe the
documented built-in set for consumers that want the catalog without mounting a
composition.
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
    TaskTool,
    WebSearchTool,
    WebTool,
    get_tool_definitions,
    get_tools_with_extensions,
)

from .bash import BashTool
from .codemode import CodemodeTool
from .edit import EditTool
from .find import FindTool
from .grep import GrepTool
from .read import ReadTool
from .skill import SkillTool
from .tool_search import ToolSearchTool
from .write import WriteTool

__all__ = [
    "DEFAULT_TOOLS",
    "PARENT_ONLY_TOOLS",
    "AskUserTool",
    "BackgroundTaskManager",
    "BackgroundTaskRecord",
    "BashTool",
    "CodemodeTool",
    "EditTool",
    "FindTool",
    "GrepTool",
    "ReadTool",
    "SkillTool",
    "TaskTool",
    "ToolSearchTool",
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
    CodemodeTool(),
    ToolSearchTool(),
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

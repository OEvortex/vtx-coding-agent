from typing import Any

from ..core.types import ToolDefinition
from .ask_user import AskUserTool
from .background import BackgroundTaskManager, BackgroundTaskRecord, get_manager, set_manager
from .base import BaseTool
from .bash import BashTool
from .edit import EditTool
from .find import FindTool
from .grep import GrepTool
from .read import ReadTool
from .skill import SkillTool
from .task import TaskTool
from .web import WebFetchTool, WebSearchTool
from .write import WriteTool

# Note: Sub-agent dispatching is shipped as a default tool in vtx.
# The generic dispatcher context (provider, model, cwd, …) that
# the tool reads lives in :mod:`vtx.dispatcher`; the runtime
# populates it on every state change. Background sub-agents are
# managed by :mod:`vtx.tools.background`, and their results are
# delivered automatically via completion notifications.

__all__ = [
    "DEFAULT_TOOLS",
    "AskUserTool",
    "BackgroundTaskManager",
    "BackgroundTaskRecord",
    "BaseTool",
    "BashTool",
    "EditTool",
    "FindTool",
    "GrepTool",
    "ReadTool",
    "SkillTool",
    "TaskTool",
    "WebFetchTool",
    "WebSearchTool",
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
    WebFetchTool(),
    WebSearchTool(),
    AskUserTool(),
    TaskTool(),
]

tools_by_name: dict[str, BaseTool] = {tool.name: tool for tool in all_tools}
DEFAULT_TOOLS: list[str] = [
    "read",
    "edit",
    "write",
    "bash",
    "find",
    "skill",
    "fetch_webpage",
    "web_search",
    "ask_user",
    "task",
]


def get_tools(names: list[str]) -> list[BaseTool]:
    return [tool for tool in all_tools if tool.name in names]


def get_tool(tool_name: str) -> BaseTool | None:
    return tools_by_name.get(tool_name)


def get_tools_with_extensions(
    default_names: list[str], extension_tools: list[BaseTool] | None = None
) -> list[BaseTool]:
    """Return the requested built-in tools plus any extension tools.

    Extension tools with the same name as a built-in win (mirrors pi's
    override behavior). Extension tools not in ``default_names`` are
    included anyway so the LLM can see and call them. ``extension_tools``
    is a no-op for backwards compatibility when no extensions are loaded.
    """
    ext_list = list(extension_tools or [])
    result: list[BaseTool] = []
    overrides: dict[str, BaseTool] = {t.name: t for t in ext_list}

    for name in default_names:
        if name in overrides:
            result.append(overrides.pop(name))
        else:
            builtin = tools_by_name.get(name)
            if builtin is not None:
                result.append(builtin)

    for tool in ext_list:
        if tool.name in {t.name for t in result}:
            continue
        result.append(tool)

    return result


_SCHEMA_DROP_KEYS = frozenset({"title", "minLength", "maxLength", "minItems", "maxItems"})


def _slim_schema(node: Any) -> Any:
    """Shrink pydantic JSON schema for the LLM: drop ``title`` and length
    constraints (still enforced by pydantic on the actual call) and collapse
    ``anyOf: [T, null]`` optional unions down to ``T``."""
    if isinstance(node, dict):
        any_of = node.get("anyOf")
        if isinstance(any_of, list):
            non_null = [s for s in any_of if s.get("type") != "null"]
            if len(non_null) == 1:
                merged = {k: v for k, v in node.items() if k != "anyOf"}
                merged.update(non_null[0])
                node = merged
        return {k: _slim_schema(v) for k, v in node.items() if k not in _SCHEMA_DROP_KEYS}
    if isinstance(node, list):
        return [_slim_schema(item) for item in node]
    return node


def get_tool_definitions(tools: list[BaseTool]) -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name=tool.name,
            description=tool.description,
            parameters=_slim_schema(tool.params.model_json_schema()),
        )
        for tool in tools
    ]

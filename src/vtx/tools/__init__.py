from ..core.types import ToolDefinition
from .base import BaseTool
from .bash import BashTool
from .edit import EditTool
from .find import FindTool
from .read import ReadTool
from .skill import SkillTool
from .web import WebFetchTool, WebSearchTool
from .write import WriteTool

__all__ = [
    "DEFAULT_TOOLS",
    "BaseTool",
    "BashTool",
    "EditTool",
    "FindTool",
    "ReadTool",
    "SkillTool",
    "WebFetchTool",
    "WebSearchTool",
    "WriteTool",
    "get_tool",
    "get_tool_definitions",
    "get_tools",
    "tools_by_name",
]

all_tools: list[BaseTool] = [
    ReadTool(),
    EditTool(),
    WriteTool(),
    BashTool(),
    FindTool(),
    SkillTool(),
    WebFetchTool(),
    WebSearchTool(),
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
]


def get_tools(names: list[str]) -> list[BaseTool]:
    return [tool for tool in all_tools if tool.name in names]


def get_tool(tool_name: str) -> BaseTool | None:
    return tools_by_name.get(tool_name)


def get_tool_definitions(tools: list[BaseTool]) -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name=tool.name,
            description=tool.description,
            parameters=tool.params.model_json_schema(),
        )
        for tool in tools
    ]

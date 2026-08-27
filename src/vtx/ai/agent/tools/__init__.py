"""Harness-level tool infrastructure and built-in harness tools.

Provides the :class:`BaseTool` contract, schema shaping for LLM tool
definitions, and core harness tools (:class:`AskUserTool`, :class:`TaskTool`,
:class:`WebTool`, :class:`GoalTool`).
"""

from typing import Any

from vtx.core.types import ToolDefinition

from ..goal.tools import GoalParams, GoalTaskItem, GoalTool
from .ask_user import AskUserParams, AskUserTool
from .base import BaseTool
from .task import SubagentSpec, TaskParams, TaskTool
from .web import SearchParams, WebSearchTool, WebTool

__all__ = [
    "AskUserParams",
    "AskUserTool",
    "BaseTool",
    "GoalParams",
    "GoalTaskItem",
    "GoalTool",
    "SearchParams",
    "SubagentSpec",
    "TaskParams",
    "TaskTool",
    "WebSearchTool",
    "WebTool",
    "get_tool_definitions",
    "get_tools_with_extensions",
    "lookup_default_tool",
    "set_default_tool_lookup",
]

_default_tool_lookup: Any = None


def get_tools_with_extensions(
    base_tools: list[str] | None = None, extension_tools: list[BaseTool] | None = None
) -> list[BaseTool]:
    """Assemble the active tool list from base tool names + extension tools."""
    tools: list[BaseTool] = []
    if base_tools is not None:
        for name in base_tools:
            tool = lookup_default_tool(name)
            if tool is not None:
                tools.append(tool)
    if extension_tools:
        tools.extend(extension_tools)
    return tools


def set_default_tool_lookup(lookup: Any) -> None:
    """Register a ``name -> BaseTool | None`` resolver used as fallback."""
    global _default_tool_lookup
    _default_tool_lookup = lookup


def lookup_default_tool(name: str) -> BaseTool | None:
    if _default_tool_lookup is None:
        return None
    return _default_tool_lookup(name)


_SCHEMA_DROP_KEYS = frozenset({"title", "minLength", "maxLength", "minItems", "maxItems"})


def _slim_schema(node: Any) -> Any:
    """Shrink pydantic JSON schema for the LLM."""
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

"""Harness-level tool infrastructure, dynamic registry, and built-in harness tools.

Provides the :class:`BaseTool` contract, schema shaping for LLM tool
definitions, dynamic tool registration/lookup APIs, and core harness tools
(:class:`AskUserTool`, :class:`TaskTool`, :class:`WebTool`, :class:`GoalTool`).
"""

from __future__ import annotations

from collections.abc import Sequence
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
    "get_all_tools",
    "get_default_tools",
    "get_parent_only_tools",
    "get_tool",
    "get_tool_definitions",
    "get_tools_with_extensions",
    "lookup_default_tool",
    "register_tool",
    "register_tools",
    "set_default_tool_lookup",
    "unregister_tool",
]

_TOOL_REGISTRY: dict[str, BaseTool] = {}
_DEFAULT_TOOL_NAMES: list[str] = []
_PARENT_ONLY_TOOL_NAMES: set[str] = set()
_default_tool_lookup: Any = None


def register_tool(
    tool: BaseTool | type[BaseTool],
    *,
    is_default: bool = False,
    parent_only: bool = False,
    override: bool = True,
) -> BaseTool:
    """Register a tool instance or class in the global harness tool registry."""
    instance = tool() if isinstance(tool, type) else tool
    name = instance.name
    if not override and name in _TOOL_REGISTRY:
        return _TOOL_REGISTRY[name]
    _TOOL_REGISTRY[name] = instance
    if is_default and name not in _DEFAULT_TOOL_NAMES:
        _DEFAULT_TOOL_NAMES.append(name)
    if parent_only:
        _PARENT_ONLY_TOOL_NAMES.add(name)
    return instance


def register_tools(
    tools: Sequence[BaseTool | type[BaseTool]],
    *,
    is_default: bool = False,
    parent_only: bool = False,
    override: bool = True,
) -> list[BaseTool]:
    """Register multiple tools in the global harness tool registry."""
    return [
        register_tool(t, is_default=is_default, parent_only=parent_only, override=override)
        for t in tools
    ]


def unregister_tool(name: str) -> None:
    """Remove a tool from the global registry."""
    _TOOL_REGISTRY.pop(name, None)
    if name in _DEFAULT_TOOL_NAMES:
        _DEFAULT_TOOL_NAMES.remove(name)
    _PARENT_ONLY_TOOL_NAMES.discard(name)


def get_tool(name: str) -> BaseTool | None:
    """Look up a registered tool by name, with fallback to legacy lookup."""
    tool = _TOOL_REGISTRY.get(name)
    if tool is not None:
        return tool
    if _default_tool_lookup is not None:
        return _default_tool_lookup(name)
    return None


# Alias for backwards compatibility
lookup_default_tool = get_tool


def get_all_tools() -> dict[str, BaseTool]:
    """Return a copy of all currently registered tools (name -> tool)."""
    return dict(_TOOL_REGISTRY)


def get_default_tools() -> list[str]:
    """Return a list of tool names marked as default."""
    return list(_DEFAULT_TOOL_NAMES)


def get_parent_only_tools() -> set[str]:
    """Return a set of tool names restricted to top-level / parent agents."""
    return set(_PARENT_ONLY_TOOL_NAMES)


def get_tools_with_extensions(
    base_tools: list[str] | None = None, extension_tools: list[BaseTool] | None = None
) -> list[BaseTool]:
    """Assemble the active tool list from base tool names + extension tools."""
    tools: list[BaseTool] = []
    target_names = base_tools if base_tools is not None else get_default_tools()
    for name in target_names:
        tool = get_tool(name)
        if tool is not None and tool not in tools:
            tools.append(tool)
    if extension_tools:
        for ext_tool in extension_tools:
            if ext_tool not in tools:
                tools.append(ext_tool)
    return tools


def set_default_tool_lookup(lookup: Any) -> None:
    """Register a fallback ``name -> BaseTool | None`` resolver."""
    global _default_tool_lookup
    _default_tool_lookup = lookup


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
    """Extract tool definitions with slimmed JSON schemas for LLM API calls."""
    defs: list[ToolDefinition] = []
    for tool in tools:
        if hasattr(tool, "parameters"):
            schema = tool.parameters
        elif hasattr(tool, "params") and hasattr(tool.params, "model_json_schema"):
            schema = tool.params.model_json_schema()
        else:
            schema = {}
        defs.append(
            ToolDefinition(
                name=tool.name, description=tool.description, parameters=_slim_schema(schema)
            )
        )
    return defs


# Register harness-native tools into the registry by default
register_tool(AskUserTool(), is_default=True, parent_only=True)
register_tool(TaskTool(), is_default=True, parent_only=False)
register_tool(WebTool(), is_default=True, parent_only=False)
register_tool(GoalTool(), is_default=True, parent_only=True)

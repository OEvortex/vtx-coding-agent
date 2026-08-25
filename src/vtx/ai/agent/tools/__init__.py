"""Harness-level tool infrastructure.

Only generic, product-agnostic pieces live here: the :class:`BaseTool`
contract and schema shaping for LLM tool definitions. Concrete built-in
tools and the default registry belong to the coding agent
(:mod:`vtx.coding_agent.tools`).
"""

from typing import Any

from vtx.core.types import ToolDefinition

from .base import BaseTool

__all__ = ["BaseTool", "get_tool_definitions", "lookup_default_tool", "set_default_tool_lookup"]

# Optional resolver for "well-known" tool names outside the explicitly
# provided tool list. The coding agent registers its built-in registry
# here at import time; the bare harness stays dependency-free.
_default_tool_lookup: Any = None


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

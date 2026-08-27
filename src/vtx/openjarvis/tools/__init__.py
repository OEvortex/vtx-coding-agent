"""OpenJarvis tools — VTX-native, registers into vtx.coding_agent."""

from __future__ import annotations

import asyncio
import importlib

# Discover OpenJarvis tools dynamically — any Tool subclass in vtx.openjarvis.tools
import pkgutil
from typing import Any

import vtx.openjarvis.tools as _pkg
from vtx.openjarvis.tools.base import Schema, Tool, tool_parameters
from vtx.openjarvis.tools.context import ToolContext
from vtx.openjarvis.tools.loader import ToolLoader
from vtx.openjarvis.tools.registry import ToolRegistry
from vtx.openjarvis.tools.schema import (
    ArraySchema,
    BooleanSchema,
    IntegerSchema,
    NumberSchema,
    ObjectSchema,
    StringSchema,
    tool_parameters_schema,
)

OPENJARVIS_TOOLS: list[Tool] = []
for _, modname, _ispkg in pkgutil.iter_modules(_pkg.__path__):
    if modname in (
        "base",
        "context",
        "loader",
        "registry",
        "schema",
        "runtime_state",
        "file_state",
        "path_utils",
        "sandbox",
    ):
        continue
    try:
        mod = importlib.import_module(f"vtx.openjarvis.tools.{modname}")
        for attr in dir(mod):
            obj = getattr(mod, attr)
            if isinstance(obj, type) and issubclass(obj, Tool) and obj is not Tool:
                # Skip abstract or helper bases
                if getattr(obj, "__abstractmethods__", None):
                    # Still try to instantiate if it has no abstract methods
                    try:
                        if len(obj.__abstractmethods__) == 0:
                            pass
                        else:
                            continue
                    except Exception:
                        continue
                try:
                    inst = obj()
                    if hasattr(inst, "name") and inst.name:
                        OPENJARVIS_TOOLS.append(inst)
                except Exception:
                    # Some tools need config — skip those that fail without args
                    continue
    except Exception:
        continue

tools_by_name = {t.name: t for t in OPENJARVIS_TOOLS if hasattr(t, "name")}
DEFAULT_TOOLS = [t.name for t in OPENJARVIS_TOOLS if hasattr(t, "name")]

# Keep VTX's filesystem etc. as fallback — will be merged
__all__ = [
    "DEFAULT_TOOLS",
    "OPENJARVIS_TOOLS",
    "ArraySchema",
    "BooleanSchema",
    "ImageGenerationTool",
    "IntegerSchema",
    "NumberSchema",
    "ObjectSchema",
    "Schema",
    "StringSchema",
    "Tool",
    "ToolContext",
    "ToolLoader",
    "ToolRegistry",
    "register_with_vtx",
    "tool_parameters",
    "tool_parameters_schema",
    "tools_by_name",
]


class _OpenJarvisBaseToolAdapter:
    """Adapt an OpenJarvis :class:`Tool` to the VTX harness ``BaseTool`` interface.

    OpenJarvis tools expose ``parameters`` (a JSON Schema) and an
    ``execute(**kwargs) -> str`` method, whereas the VTX agent expects a
    ``BaseTool`` with a pydantic ``params`` model and an
    ``execute(params, ...) -> ToolResult`` method. This wrapper bridges the
    two so OpenJarvis tools can be called by the VTX agent (e.g. when the
    model invokes ``apply_patch``).
    """

    def __init__(self, oj_tool: Tool) -> None:
        from vtx.ai.agent.extensions import _json_schema_to_pydantic
        from vtx.core.types import ToolResult

        self._oj_tool = oj_tool
        self._tool_result = ToolResult
        self.name = oj_tool.name
        self.description = oj_tool.description
        self.params = _json_schema_to_pydantic(oj_tool.name, oj_tool.parameters)
        self.mutating = not oj_tool.read_only
        self.read_only = oj_tool.read_only
        self.prompt_guidelines = tuple(getattr(oj_tool, "prompt_guidelines", []) or [])

    async def execute(
        self,
        params: Any,
        cancel_event: asyncio.Event | None = None,
        tool_call_id: str | None = None,
    ) -> Any:
        kwargs = params.model_dump(exclude_none=True)
        result = await self._oj_tool.execute(**kwargs)
        if isinstance(result, str):
            success = not result.startswith("Error")
            return self._tool_result(success=success, result=result)
        return self._tool_result(success=True, result=str(result))

    def format_call(self, params: Any) -> str:
        data = params.model_dump(exclude_none=True)
        if not data:
            return ""
        return " / ".join(f"{k}={v}" for k, v in data.items())

    def format_preview(self, params: Any) -> str | None:
        return None


def register_with_vtx() -> None:
    """Register OpenJarvis tools into vtx.coding_agent + vtx.tui.

    Merges OpenJarvis tools on top of VTX's so the TUI shows a single
    Tools block (no dupe) with both surfaces available. VTX's built-ins
    (read/edit/write/bash/find/skill/web/ask_user/task/goal) remain,
    plus OpenJarvis's cron/message/mcp/long_task etc.
    """
    import vtx.coding_agent.tools as vtx_tools

    try:
        import vtx.tui.app as vtx_app
    except ImportError:
        import vtx.ui.app as vtx_app  # type: ignore

    from vtx.ai.agent.tools import BaseTool

    # Wrap OpenJarvis tools into the VTX harness BaseTool interface so the
    # agent can call them (it expects ``.params`` + ``execute(params, ...)``).
    wrapped: dict[str, BaseTool] = {}
    for name, tool in tools_by_name.items():
        if isinstance(tool, BaseTool):
            wrapped[name] = tool
        else:
            wrapped[name] = _OpenJarvisBaseToolAdapter(tool)

    # Merge tool dicts — OpenJarvis wins on name collision
    merged = dict(vtx_tools.tools_by_name)
    merged.update(wrapped)
    vtx_tools.tools_by_name = merged
    # Also update the tui's reference if it has its own copy
    if hasattr(vtx_app, "tools_by_name"):
        vtx_app.tools_by_name = merged

    # Expose wrapped tools to the agent's default tool list so they appear in
    # the LLM schema and are returned by get_tools().
    for name in wrapped:
        if name not in vtx_tools.all_tools_set:
            vtx_tools.all_tools.append(wrapped[name])
            vtx_tools.all_tools_set.add(name)

    # Merge DEFAULT_TOOLS — keep VTX's order, then append OpenJarvis's not already there
    merged_defaults = list(vtx_tools.DEFAULT_TOOLS)
    for name in DEFAULT_TOOLS:
        if name not in merged_defaults:
            merged_defaults.append(name)
    vtx_tools.DEFAULT_TOOLS = merged_defaults
    if hasattr(vtx_app, "DEFAULT_TOOLS"):
        vtx_app.DEFAULT_TOOLS = merged_defaults

    # Also patch the harness-level tool lookup so headless runs resolve OpenJarvis tools
    try:
        from vtx.ai.agent.tools import set_default_tool_lookup

        def _lookup(name: str):
            return merged.get(name)

        set_default_tool_lookup(_lookup)
    except Exception:
        pass

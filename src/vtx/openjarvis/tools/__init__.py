"""OpenJarvis tools — VTX-native, registers into vtx.coding_agent."""

from __future__ import annotations

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

# Discover OpenJarvis tools dynamically — any Tool subclass in vtx.openjarvis.tools
import pkgutil
import importlib

import vtx.openjarvis.tools as _pkg
from vtx.openjarvis.tools.base import Tool

OPENJARVIS_TOOLS: list[Tool] = []
for _, modname, ispkg in pkgutil.iter_modules(_pkg.__path__):
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
    "ArraySchema",
    "BooleanSchema",
    "DEFAULT_TOOLS",
    "ImageGenerationTool",
    "IntegerSchema",
    "NumberSchema",
    "OPENJARVIS_TOOLS",
    "ObjectSchema",
    "Schema",
    "StringSchema",
    "Tool",
    "ToolContext",
    "ToolLoader",
    "ToolRegistry",
    "tools_by_name",
    "tool_parameters",
    "tool_parameters_schema",
    "register_with_vtx",
]


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

    # Merge tool dicts — OpenJarvis wins on name collision
    merged = dict(vtx_tools.tools_by_name)
    merged.update(tools_by_name)
    vtx_tools.tools_by_name = merged
    # Also update the tui's reference if it has its own copy
    if hasattr(vtx_app, "tools_by_name"):
        vtx_app.tools_by_name = merged

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

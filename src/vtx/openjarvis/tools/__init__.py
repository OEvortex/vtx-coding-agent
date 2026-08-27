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


# Curated set of OpenJarvis tools exposed to the agent. Every other
# OpenJarvis tool (edit_file, read_file, write_file, list_dir, find_files,
# grep, web_search, ...) is dropped so the model only sees this surface.
OPENJARVIS_KEEP_TOOLS = frozenset(
    {"exec", "apply_patch", "list_exec_sessions", "write_stdin", "message"}
)

# Final ordered tool list handed to the OpenJarvis agent. ``exec`` replaces
# ``bash``; ``find``/``skill`` are retained from VTX's built-ins.
OPENJARVIS_DEFAULT_TOOLS = [
    "read",
    "edit",
    "write",
    "exec",
    "apply_patch",
    "find",
    "skill",
    "web",
    "ask_user",
    "task",
    "goal",
    "list_exec_sessions",
    "write_stdin",
    "message",
]


def register_with_vtx() -> None:
    """Register OpenJarvis tools into vtx.coding_agent + vtx.tui.

    Only the curated subset in :data:`OPENJARVIS_KEEP_TOOLS` is exposed; all
    other OpenJarvis tools are dropped. ``exec`` replaces VTX's ``bash`` built-in.
    """
    import vtx.coding_agent.tools as vtx_tools

    try:
        import vtx.tui.app as vtx_app
    except ImportError:
        import vtx.ui.app as vtx_app  # type: ignore

    from vtx.ai.agent.tools import BaseTool

    # Wrap only the kept OpenJarvis tools into the VTX harness BaseTool interface
    # (it expects ``.params`` + ``execute(params, ...)``). Others are discarded.
    wrapped: dict[str, BaseTool] = {}
    for name, tool in tools_by_name.items():
        if name not in OPENJARVIS_KEEP_TOOLS:
            continue
        if isinstance(tool, BaseTool):
            wrapped[name] = tool
        else:
            wrapped[name] = _OpenJarvisBaseToolAdapter(tool)

    # Restrict the merged registry to exactly the curated tool set (plus the
    # wrapped OpenJarvis tools); everything else — including VTX's ``bash`` and
    # ``grep`` — is dropped in favour of ``exec`` and the curated list.
    keep_names = set(OPENJARVIS_DEFAULT_TOOLS)
    merged = {n: t for n, t in vtx_tools.tools_by_name.items() if n in keep_names}
    merged.update(wrapped)
    vtx_tools.tools_by_name = merged
    # Also update the tui's reference if it has its own copy
    if hasattr(vtx_app, "tools_by_name"):
        vtx_app.tools_by_name = merged

    # Expose only the curated tools on the agent's tool list.
    vtx_tools.all_tools = [t for t in vtx_tools.all_tools if t.name in keep_names]
    vtx_tools.all_tools_set = {t.name for t in vtx_tools.all_tools}
    for name in wrapped:
        if name not in vtx_tools.all_tools_set:
            vtx_tools.all_tools.append(wrapped[name])
            vtx_tools.all_tools_set.add(name)

    # Set the curated default tool list (idempotent).
    vtx_tools.DEFAULT_TOOLS = list(OPENJARVIS_DEFAULT_TOOLS)
    if hasattr(vtx_app, "DEFAULT_TOOLS"):
        vtx_app.DEFAULT_TOOLS = list(OPENJARVIS_DEFAULT_TOOLS)

    # Also patch the harness-level tool lookup so headless runs resolve OpenJarvis tools
    try:
        from vtx.ai.agent.tools import set_default_tool_lookup

        def _lookup(name: str):
            return merged.get(name)

        set_default_tool_lookup(_lookup)
    except Exception:
        pass

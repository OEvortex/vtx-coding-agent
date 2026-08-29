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
    "adapt_tool",
    "register_with_vtx",
    "tool_parameters",
    "tool_parameters_schema",
    "tools_by_name",
]


class _OpenJarvisBaseToolAdapter:
    """Adapt OpenJarvis or VTX tools to the unified pi-style interface.

    Bridges tool schemas, tool icons, formatted calls, previews, and pi-style
    result cards for all tools used within OpenJarvis.
    """

    def __init__(self, raw_tool: Any) -> None:
        from vtx.ai.agent.extensions import _json_schema_to_pydantic
        from vtx.ai.agent.tools.base import BaseTool
        from vtx.core.types import ToolResult
        from vtx.openjarvis.tui.tool_ui import tool_icon

        self._raw_tool = raw_tool
        self._is_vtx_tool = isinstance(raw_tool, BaseTool)
        self._tool_result = ToolResult
        self.name = raw_tool.name
        self.description = getattr(raw_tool, "description", "")
        if hasattr(raw_tool, "params") and raw_tool.params is not None:
            self.params = raw_tool.params
        elif hasattr(raw_tool, "parameters"):
            self.params = _json_schema_to_pydantic(raw_tool.name, raw_tool.parameters)
        else:
            self.params = getattr(raw_tool, "params", None)

        self.mutating = getattr(raw_tool, "mutating", not getattr(raw_tool, "read_only", False))
        self.read_only = getattr(raw_tool, "read_only", not self.mutating)
        self.tool_icon = tool_icon(raw_tool.name, raw_tool)
        self.needs_approval = getattr(raw_tool, "needs_approval", False)
        self.ui_block = getattr(raw_tool, "ui_block", None)
        self.prompt_guidelines = tuple(getattr(raw_tool, "prompt_guidelines", []) or [])

    async def execute(
        self,
        params: Any,
        cancel_event: asyncio.Event | None = None,
        tool_call_id: str | None = None,
    ) -> Any:
        import time

        from vtx.openjarvis.tui.tool_ui import build_result_ui

        kwargs = (
            params.model_dump(exclude_none=True)
            if hasattr(params, "model_dump")
            else (params if isinstance(params, dict) else {})
        )
        started = time.monotonic()

        images = None
        file_changes = None
        if self._is_vtx_tool:
            import inspect

            sig = inspect.signature(self._raw_tool.execute)
            call_kwargs = {}
            if "cancel_event" in sig.parameters:
                call_kwargs["cancel_event"] = cancel_event
            if "tool_call_id" in sig.parameters:
                call_kwargs["tool_call_id"] = tool_call_id

            raw_res = await self._raw_tool.execute(params, **call_kwargs)
            elapsed = time.monotonic() - started
            if hasattr(raw_res, "result"):
                result_str = str(raw_res.result)
                success = getattr(raw_res, "success", True)
                images = getattr(raw_res, "images", None)
                file_changes = getattr(raw_res, "file_changes", None)
            else:
                result_str = str(raw_res)
                success = not result_str.startswith("Error")
        else:
            raw_res = await self._raw_tool.execute(**kwargs)
            elapsed = time.monotonic() - started
            result_str = str(raw_res) if not isinstance(raw_res, str) else raw_res
            success = not result_str.startswith("Error")

        ui = build_result_ui(
            result_str, success, elapsed_s=elapsed, tool_name=self.name, tool_data=kwargs
        )
        return self._tool_result(
            success=success,
            result=result_str,
            ui_summary=ui["ui_summary"],
            ui_details=ui["ui_details"],
            ui_details_full=ui["ui_details_full"],
            images=images,
            file_changes=file_changes,
        )

    def format_call(self, params: Any) -> str:
        from vtx.openjarvis.tui.tool_ui import format_call as _format_call

        data = params.model_dump(exclude_none=True) if hasattr(params, "model_dump") else {}
        custom = _format_call(self.name, data)
        if custom:
            return custom
        if hasattr(self._raw_tool, "format_call"):
            return self._raw_tool.format_call(params)
        return ""

    def format_preview(self, params: Any) -> str | None:
        from vtx.openjarvis.tui.tool_ui import format_preview as _format_preview

        data = params.model_dump(exclude_none=True) if hasattr(params, "model_dump") else {}
        custom = _format_preview(self.name, data)
        if custom is not None:
            return custom
        if hasattr(self._raw_tool, "format_preview"):
            return self._raw_tool.format_preview(params)
        return None


def adapt_tool(tool: Any) -> Any:
    """Wrap any OpenJarvis or VTX tool for unified pi-style interface."""
    if isinstance(tool, _OpenJarvisBaseToolAdapter):
        return tool
    return _OpenJarvisBaseToolAdapter(tool)


# Curated set of OpenJarvis tools exposed to the agent. Every other
# OpenJarvis tool (edit_file, read_file, write_file, list_dir, find_files,
# grep, web_search, ...) is dropped so the model only sees this surface.
OPENJARVIS_KEEP_TOOLS = frozenset(
    {"exec", "apply_patch", "list_exec_sessions", "write_stdin", "message", "cron"}
)

# OpenJarvis-native duplicates of VTX built-ins (read_file vs read, grep vs
# grep, ...). Dropped from the agent surface; the VTX versions are kept.
OPENJARVIS_DROP_TOOLS = frozenset(
    {"read_file", "write_file", "edit_file", "list_dir", "find_files", "grep", "web_search"}
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
    "cron",
    "list_exec_sessions",
    "write_stdin",
    "message",
]


def register_with_vtx() -> None:
    """Register OpenJarvis tools into vtx.coding_agent + vtx.tui with full pi-style UX."""
    import vtx.coding_agent.tools as vtx_tools

    try:
        import vtx.tui.app as vtx_app
    except ImportError:
        import vtx.ui.app as vtx_app  # type: ignore

    # Wrapped OpenJarvis tools
    wrapped: dict[str, Any] = {}
    for name, tool in tools_by_name.items():
        if name not in OPENJARVIS_KEEP_TOOLS:
            continue
        wrapped[name] = _OpenJarvisBaseToolAdapter(tool)

    # ``cron`` needs a CronService so it can't be auto-discovered
    if "cron" in OPENJARVIS_DEFAULT_TOOLS and "cron" not in wrapped:
        try:
            from pathlib import Path

            from vtx.core.paths import get_config_dir
            from vtx.openjarvis.cron.service import CronService
            from vtx.openjarvis.tools.cron import CronTool

            cron_service = CronService(
                store_path=Path(get_config_dir()) / "openjarvis" / "cron.json"
            )
            wrapped["cron"] = _OpenJarvisBaseToolAdapter(CronTool(cron_service=cron_service))
        except Exception:
            pass

    # Restrict and adapt every tool in the curated list with pi-style UX
    keep_names = set(OPENJARVIS_DEFAULT_TOOLS)
    merged: dict[str, Any] = {}
    for name in OPENJARVIS_DEFAULT_TOOLS:
        raw = wrapped.get(name) or vtx_tools.tools_by_name.get(name)
        if raw is not None:
            merged[name] = adapt_tool(raw)

    vtx_tools.tools_by_name = merged
    if hasattr(vtx_app, "tools_by_name"):
        vtx_app.tools_by_name = merged

    # Expose only the curated tools on the agent's tool list.
    vtx_tools.all_tools = [merged[name] for name in OPENJARVIS_DEFAULT_TOOLS if name in merged]
    vtx_tools.all_tools_set = {t.name for t in vtx_tools.all_tools}

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

    # Patch the harness-level registry itself
    try:
        import vtx.ai.agent.tools as _harness

        parent_only = _harness.get_parent_only_tools()
        for name in list(_harness.get_all_tools()):
            if name not in keep_names:
                _harness.unregister_tool(name)
        for name in OPENJARVIS_DEFAULT_TOOLS:
            tool = merged.get(name) or _harness.get_all_tools().get(name)
            if tool is not None:
                _harness.register_tool(tool, is_default=True, parent_only=name in parent_only)
    except Exception:
        pass

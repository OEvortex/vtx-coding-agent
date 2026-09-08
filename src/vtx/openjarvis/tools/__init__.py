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
        on_output: Any = None,
        **extra: Any,
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
            if "on_output" in sig.parameters and on_output is not None:
                call_kwargs["on_output"] = on_output

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
            if on_output is not None:
                kwargs["on_output"] = on_output
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
# ``skill`` is OpenJarvis-isolated (hides VTX builtin_skills at
# src/vtx/coding_agent/builtin_skills/ — modal, review, etc.).
OPENJARVIS_KEEP_TOOLS = frozenset(
    {"exec", "apply_patch", "list_exec_sessions", "write_stdin", "message", "cron", "skill"}
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
    "my",
]


def _build_openjarvis_merged(
    cron_service: Any | None = None, *, include_extras: bool = True
) -> dict[str, Any]:
    """Build OpenJarvis curated tool map WITHOUT mutating VTX globals.

    Read-only from ``vtx.coding_agent.tools.tools_by_name`` and
    ``tools_by_name`` (OpenJarvis native). Used by ``OpenJarvisRuntime``.
    When ``include_extras`` is True (default), also loads OpenJarvis-native
    extras (my, run_cli_app, long_task, generate_image, complete_goal, and
    any future user-added tools) via :class:`ToolLoader`, matching
    ``OpenJarvisRuntime.build_tools``. This keeps the TUI (register_with_vtx)
    and gateway in sync.
    """
    import vtx.coding_agent.tools as vtx_tools

    wrapped: dict[str, Any] = {}
    for name, tool in tools_by_name.items():
        if name not in OPENJARVIS_KEEP_TOOLS:
            continue
        wrapped[name] = _OpenJarvisBaseToolAdapter(tool)

    if "cron" in OPENJARVIS_DEFAULT_TOOLS and "cron" not in wrapped:
        try:
            from pathlib import Path as _Path

            from vtx.core.paths import get_config_dir as _get_config_dir
            from vtx.openjarvis.cron.service import CronService as _CronService
            from vtx.openjarvis.tools.cron import CronTool as _CronTool

            svc = cron_service
            if svc is None:
                svc = _CronService(
                    store_path=_Path(_get_config_dir()) / "openjarvis" / "cron.json"
                )
            wrapped["cron"] = _OpenJarvisBaseToolAdapter(_CronTool(cron_service=svc))
        except Exception:
            pass

    merged: dict[str, Any] = {}
    for name in OPENJARVIS_DEFAULT_TOOLS:
        raw = wrapped.get(name) or vtx_tools.tools_by_name.get(name)
        if raw is not None:
            merged[name] = adapt_tool(raw)

    if include_extras:
        try:
            from pathlib import Path as _Path2

            from vtx.openjarvis.tools.context import ToolContext
            from vtx.openjarvis.tools.loader import ToolLoader
            from vtx.openjarvis.tools.registry import ToolRegistry

            # Build a minimal ToolContext for discovery (mirrors runtime._tool_config_view)
            tctx = None
            try:
                from vtx.openjarvis.agent.runtime import _ToolConfigView
                from vtx.openjarvis.tools.cli_apps import CliAppsToolConfig
                from vtx.openjarvis.tools.filesystem import FileToolsConfig
                from vtx.openjarvis.tools.image_generation import ImageGenerationToolConfig
                from vtx.openjarvis.tools.self import MyToolConfig
                from vtx.openjarvis.tools.shell import ExecToolConfig
                from vtx.openjarvis.tools.web import WebToolsConfig

                try:
                    from vtx.openjarvis.agent.config import OpenJarvisConfig

                    cfg = OpenJarvisConfig.load()
                    raw = dict(getattr(cfg, "tools", None) or {})
                except Exception:
                    raw = {}
                view = _ToolConfigView(
                    restrict_to_workspace=bool(raw.get("restrict_to_workspace", False)),
                    exec=ExecToolConfig(**(raw.get("exec") or {})),
                    file=FileToolsConfig(**(raw.get("file") or {})),
                    web=WebToolsConfig(**(raw.get("web") or {})),
                    my=MyToolConfig(**(raw.get("my") or {})),
                    image_generation=ImageGenerationToolConfig(
                        **(raw.get("image_generation") or {})
                    ),
                    cli_apps=CliAppsToolConfig(**(raw.get("cli_apps") or {})),
                )
                tctx = ToolContext(
                    config=view,
                    workspace=str(_Path2.cwd()),
                    bus=None,
                    cron_service=cron_service,
                    sessions={},
                )
            except Exception:
                # Fallback: minimal ctx with dummy config that enables everything
                class _DummyCfg:
                    restrict_to_workspace = False
                    exec = type("o", (), {"enable": True})()
                    file = type("o", (), {"enable": True})()
                    web = type("o", (), {"enable": True})()
                    my = type("o", (), {"enable": True, "allow_set": False})()
                    image_generation = type("o", (), {"enabled": False})()
                    cli_apps = type("o", (), {"enable": True})()

                tctx = ToolContext(
                    config=_DummyCfg(),  # type: ignore[arg-type]
                    workspace=str(_Path2.cwd()),
                    bus=None,
                    cron_service=cron_service,
                    sessions={},
                )
            registry = ToolRegistry()
            try:
                ToolLoader().load(tctx, registry)
            except Exception:
                registry = ToolRegistry()
            present = set(merged.keys())
            for name in registry.tool_names:
                if name in OPENJARVIS_DROP_TOOLS:
                    continue
                if name in present:
                    # Keep the runtime-wired cron if present
                    if name == "cron" and "cron" in merged:
                        # prefer merged's cron (already wired) — skip
                        continue
                    continue
                tool = registry.get(name)
                if tool is None:
                    continue
                merged[name] = adapt_tool(tool)
                present.add(name)
        except Exception:
            pass
    return merged


def get_openjarvis_tools(cron_service: Any | None = None) -> list[Any]:
    """Curated OpenJarvis tool list for ``VtxAgent`` — no global side-effects."""
    merged = _build_openjarvis_merged(cron_service=cron_service, include_extras=True)
    # Preserve curated ordering for DEFAULT, then append extras in sorted order for determinism.
    ordered = [merged[name] for name in OPENJARVIS_DEFAULT_TOOLS if name in merged]
    extras = [v for k, v in merged.items() if k not in set(OPENJARVIS_DEFAULT_TOOLS)]
    extras.sort(key=lambda t: t.name)
    return ordered + extras


def get_openjarvis_tool_map(cron_service: Any | None = None) -> dict[str, Any]:
    """Curated tool map (name -> adapted tool) — read-only, no VTX mutation."""
    return _build_openjarvis_merged(cron_service=cron_service, include_extras=True)


def register_with_vtx() -> None:
    """Legacy: Register OpenJarvis tools into VTX globals (monkey-patch).

    .. deprecated::
        OpenJarvis runtime no longer calls this. It mutates
        ``vtx.coding_agent.tools`` and ``vtx.ai.agent.tools`` globals.
        Prefer :func:`get_openjarvis_tools` / :func:`get_openjarvis_tool_map`
        which are side-effect free. Kept for tests and backwards compat.
    """
    import vtx.coding_agent.tools as vtx_tools

    try:
        import vtx.tui.app as vtx_app
    except ImportError:
        import vtx.ui.app as vtx_app  # type: ignore

    merged = _build_openjarvis_merged(include_extras=True)
    # Curated defaults only on the public surface; extras stay in all_tools.
    keep_names = set(OPENJARVIS_DEFAULT_TOOLS)
    extras_sorted = sorted(k for k in merged if k not in keep_names)

    vtx_tools.tools_by_name = {
        name: merged[name] for name in OPENJARVIS_DEFAULT_TOOLS if name in merged
    }
    if hasattr(vtx_app, "tools_by_name"):
        vtx_app.tools_by_name = vtx_tools.tools_by_name

    ordered = [merged[name] for name in OPENJARVIS_DEFAULT_TOOLS if name in merged]
    extras = [merged[name] for name in extras_sorted]
    vtx_tools.all_tools = ordered + extras
    vtx_tools.all_tools_set = {t.name for t in vtx_tools.all_tools}
    # DEFAULT_TOOLS stays curated (16); all_tools carries the full set (21).
    vtx_tools.DEFAULT_TOOLS = list(OPENJARVIS_DEFAULT_TOOLS)
    if hasattr(vtx_app, "DEFAULT_TOOLS"):
        vtx_app.DEFAULT_TOOLS = list(OPENJARVIS_DEFAULT_TOOLS)

    try:
        from vtx.ai.agent.tools import set_default_tool_lookup

        def _lookup(name: str):
            return merged.get(name) if name in keep_names else None

        set_default_tool_lookup(_lookup)
    except Exception:
        pass

    try:
        import vtx.ai.agent.tools as _harness

        parent_only = _harness.get_parent_only_tools()
        for name in list(_harness.get_all_tools()):
            if name not in keep_names:
                _harness.unregister_tool(name)
        for name in list(merged.keys()):
            tool = merged.get(name) or _harness.get_all_tools().get(name)
            if tool is not None:
                # Only DEFAULT tools are marked is_default; extras are opt-in.
                is_def = name in set(OPENJARVIS_DEFAULT_TOOLS)
                _harness.register_tool(tool, is_default=is_def, parent_only=name in parent_only)
    except Exception:
        pass

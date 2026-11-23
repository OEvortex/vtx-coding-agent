"""The Ctx coding agent's built-in tools.

Each tool module here owns both its implementation and its plugin row (the
``PLUGIN`` export), so all registration goes through the plugins system:
:mod:`ctx.coding_agent.builtin_rows` contributes these rows into the harness's
``BASE``/``MINIMAL`` bundles, and mounting the composition is what puts the
tools in the registry. Importing this package registers nothing.

Re-exports resolve lazily through :func:`__getattr__` so importing one tool
submodule (e.g. ``ctx.coding_agent.tools.todo`` for the panel) does not pay
for the whole catalog — ``all_tools``/``tools_by_name`` are built on first
access.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ctx.agent.tools.base import BaseTool

    all_tools: list[BaseTool]
    tools_by_name: dict[str, BaseTool]
    DEFAULT_TOOLS: list[str]
    PARENT_ONLY_TOOLS: frozenset[str]

    def get_tools(names: list[str]) -> list[BaseTool]: ...
    def get_tool(tool_name: str) -> BaseTool | None: ...


_TOOL_MODULES = {
    "ApplyPatchTool": ".apply_patch",
    "AskUserTool": ".ask_user",
    "BashTool": ".bash",
    "CodemodeTool": ".codemode",
    "GrepTool": ".grep",
    "ReadTool": ".read",
    "SkillTool": ".skill",
    "TaskTool": ".subagent",
    "TodoTool": ".todo",
    "ToolSearchTool": ".tool_search",
    "WebSearchTool": ".web",
    "WebTool": ".web",
}
_AGENT_TOOLS = {"BaseTool", "get_tool_definitions", "get_tools_with_extensions"}
_BACKGROUND = {"BackgroundTaskManager", "BackgroundTaskRecord", "get_manager", "set_manager"}
_CATALOG = {
    "DEFAULT_TOOLS",
    "PARENT_ONLY_TOOLS",
    "all_tools",
    "get_tool",
    "get_tools",
    "tools_by_name",
}


def _agent_tools_module():
    from ctx.agent import tools as module

    return module


def _build_catalog() -> None:
    """Populate module globals with the tool catalog on first access."""
    global all_tools, tools_by_name, DEFAULT_TOOLS, PARENT_ONLY_TOOLS, get_tools, get_tool
    import importlib

    modules = [
        importlib.import_module(".read", __name__),
        importlib.import_module(".apply_patch", __name__),
        importlib.import_module(".bash", __name__),
        importlib.import_module(".grep", __name__),
        importlib.import_module(".skill", __name__),
        importlib.import_module(".todo", __name__),
        importlib.import_module(".web", __name__),
        importlib.import_module(".ask_user", __name__),
        importlib.import_module(".subagent", __name__),
    ]
    all_tools = [
        modules[0].ReadTool(),
        modules[1].ApplyPatchTool(),
        modules[2].BashTool(),
        modules[3].GrepTool(),
        modules[4].SkillTool(),
        modules[5].TodoTool(),
        modules[6].WebTool(),
        modules[6].WebSearchTool(),
        modules[7].AskUserTool(),
        modules[8].TaskTool(),
        importlib.import_module(".codemode", __name__).CodemodeTool(),
        importlib.import_module(".tool_search", __name__).ToolSearchTool(),
    ]
    tools_by_name = {tool.name: tool for tool in all_tools}
    DEFAULT_TOOLS = [
        "read",
        "apply_patch",
        "bash",
        "skill",
        "todo",
        "web",
        "ask_user",
        "delegate_subagent",
    ]
    PARENT_ONLY_TOOLS = frozenset({"delegate_subagent"})

    def get_tools(names: list[str]):
        return [tool for tool in all_tools if tool.name in names]

    def get_tool(tool_name: str):
        return tools_by_name.get(tool_name)

    globals()["get_tools"] = get_tools
    globals()["get_tool"] = get_tool


def __getattr__(name: str) -> Any:
    if name in _TOOL_MODULES:
        import importlib

        module = importlib.import_module(_TOOL_MODULES[name], __name__)
        return getattr(module, name)
    if name in _AGENT_TOOLS:
        return getattr(_agent_tools_module(), name)
    if name in _BACKGROUND:
        from ctx.agent import background as module

        return getattr(module, name)
    if name in _CATALOG:
        if "all_tools" not in globals():
            _build_catalog()
        return globals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "DEFAULT_TOOLS",
    "PARENT_ONLY_TOOLS",
    "ApplyPatchTool",
    "AskUserTool",
    "BackgroundTaskManager",
    "BackgroundTaskRecord",
    "BashTool",
    "CodemodeTool",
    "GrepTool",
    "ReadTool",
    "SkillTool",
    "TaskTool",
    "TodoTool",
    "ToolSearchTool",
    "WebSearchTool",
    "WebTool",
    "get_manager",
    "get_tool",
    "get_tool_definitions",
    "get_tools",
    "get_tools_with_extensions",
    "set_manager",
    "tools_by_name",
]

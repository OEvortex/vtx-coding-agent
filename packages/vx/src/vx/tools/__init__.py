"""Built-in tools for the vx agent.

Each tool is a self-contained implementation inspired by the ctx.code.tools
versions but owned by vx. Tools use the ctx harness infrastructure
(BaseTool, ToolResult) and register via the plugin system.

Importing this module registers nothing. Use :func:`get_tools` or
:func:`register_all` to access the tool instances.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ctx.agent.tools.base import BaseTool

    all_tools: list[BaseTool]
    tools_by_name: dict[str, BaseTool]
    DEFAULT_TOOLS: list[str]

    def get_tools(names: list[str] | None = None) -> list[BaseTool]: ...


_TOOL_MODULES = {
    "ApplyPatchTool": ".apply_patch",
    "AskUserTool": ".ask_user",
    "BashTool": ".bash",
    "BoardTool": ".swarm",
    "ReadTool": ".read",
    "SearchTool": ".search",
    "SkillTool": ".skill",
    "SubagentTool": ".subagent",
    "SwarmTool": ".swarm",
    "TodoTool": ".todo",
    "WebTool": ".web",
}

#: Tools only the top-level agent gets. A sub-agent dispatched by
#: ``delegate_subagent`` must not start its own swarm or recurse, and the board
#: tools belong to a conversation's swarm, not to a one-shot helper.
PARENT_ONLY = frozenset({"swarm", "board", "delegate_subagent"})


def _build_catalog() -> None:
    """Populate module globals with the tool catalog on first access."""
    global all_tools, tools_by_name, DEFAULT_TOOLS, get_tools
    import importlib

    modules = [
        importlib.import_module(".read", __name__),
        importlib.import_module(".apply_patch", __name__),
        importlib.import_module(".bash", __name__),
        importlib.import_module(".web", __name__),
        importlib.import_module(".skill", __name__),
        importlib.import_module(".ask_user", __name__),
        importlib.import_module(".todo", __name__),
        importlib.import_module(".search", __name__),
        importlib.import_module(".subagent", __name__),
        importlib.import_module(".swarm", __name__),
    ]
    all_tools = [
        modules[0].ReadTool(),
        modules[1].ApplyPatchTool(),
        modules[2].BashTool(),
        modules[3].WebTool(),
        modules[4].SkillTool(),
        modules[5].AskUserTool(),
        modules[6].TodoTool(),
        modules[7].SearchTool(),
        modules[8].SubagentTool(),
        modules[9].SwarmTool(),
        modules[9].BoardTool(),
    ]
    tools_by_name = {tool.name: tool for tool in all_tools}
    DEFAULT_TOOLS = [
        "read",
        "apply_patch",
        "bash",
        "web",
        "skill",
        "search",
        "ask_user",
        "todo",
        "delegate_subagent",
        "swarm",
        "board",
    ]

    def get_tools(names: list[str] | None = None):
        if names is None:
            return list(all_tools)
        return [tool for tool in all_tools if tool.name in names]

    globals()["get_tools"] = get_tools


def register_all() -> list[BaseTool]:
    """Register all vx tools in the harness tool registry and return the instances."""
    if "all_tools" not in globals():
        _build_catalog()
    from ctx.agent.tools import register_tools

    top_level = [tool for tool in all_tools if tool.name in PARENT_ONLY]
    rest = [tool for tool in all_tools if tool.name not in PARENT_ONLY]
    register_tools(rest, is_default=True)
    register_tools(top_level, is_default=True, parent_only=True)
    return all_tools


def _catalog_getattr(name: str) -> Any | None:
    catalog_names = {"all_tools", "tools_by_name", "DEFAULT_TOOLS", "get_tools"}
    if name in catalog_names:
        if "all_tools" not in globals():
            _build_catalog()
        return globals().get(name)
    if name in _TOOL_MODULES:
        import importlib

        module = importlib.import_module(_TOOL_MODULES[name], __name__)
        return getattr(module, name)
    return None


def __getattr__(name: str) -> Any:
    result = _catalog_getattr(name)
    if result is not None:
        return result
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "DEFAULT_TOOLS",
    "PARENT_ONLY",
    "all_tools",
    "get_tools",
    "register_all",
    "tools_by_name",
]

"""Compose the active tool/command set from a base + the active agent.

The active tool set is::

    built_in_tools (filtered by DEFAULT_TOOLS or runtime override)
  + session_extensions.tools                    (ExtensionAPI.register_tool)
  + active_agent.definition.extensions[].tools (loaded extensions per agent)
  + active_agent.local_tools                   (AgentAPI.local_tool)
  - active_agent.tools_deny
  intersect with active_agent.tools_allow (when non-empty)

Same formula for commands.
"""

from __future__ import annotations

# ``ExtensionCommand`` is imported lazily in :func:`compose_active_commands`
# to avoid a circular import at module load (extensions -> tools -> agents
# -> activate -> extensions).
from ..tools import BaseTool
from .api import LoadedAgent
from .registry import AgentRegistry


def _filter(
    base_names: list[str],
    base_pool: dict[str, BaseTool],
    extra_pool: dict[str, BaseTool],
    allow: list[str] | None,
    deny: list[str],
    *,
    always_keep: set[str] | None = None,
) -> list[BaseTool]:
    seen: set[str] = set()
    result: list[BaseTool] = []
    always_keep = always_keep or set()

    # 1. Built-in tools named in base_names (or in allow)
    to_load = list(base_names)
    if allow:
        for n in allow:
            if n in base_pool and n not in to_load:
                to_load.append(n)

    for n in to_load:
        if n in seen:
            continue
        tool = base_pool.get(n)
        if tool is not None:
            result.append(tool)
            seen.add(n)

    # 2. Extra tools (extension tools + agent-scoped local tools)
    for n, tool in extra_pool.items():
        if n in seen:
            continue
        result.append(tool)
        seen.add(n)

    # 3. Apply deny. The ``always_keep`` set exempts tools the agent
    # explicitly contributed: a local tool registered via
    # ``api.local_tool`` is part of the agent's profile and must not be
    # stripped by the agent's own deny list. (Mirrors how extension
    # tools win over built-ins — the agent's contributions are first-class.)
    if deny:
        deny_set = set(deny) - always_keep
        result = [t for t in result if t.name not in deny_set]

    # 4. Apply allow (if non-empty, intersect). Tools in ``always_keep``
    # are exempt so the agent can ship its own local tools alongside a
    # restrictive allow list.
    if allow:
        allow_set = set(allow) | always_keep
        result = [t for t in result if t.name in allow_set]

    return result


def compose_active_tools(
    *,
    base_tool_names: list[str],
    base_tool_pool: dict[str, BaseTool],
    extension_tools: list[BaseTool],
    active_agent: LoadedAgent | None,
) -> list[BaseTool]:
    """Compute the active tool list for the runtime.

    ``extension_tools`` should be the full list of tools contributed by
    session-scoped extensions (``ExtensionAPI.register_tool``), already
    with extension-wins-over-built-in ordering applied.
    """
    extra: dict[str, BaseTool] = {t.name: t for t in extension_tools}
    if active_agent is not None:
        for t in active_agent.local_tools.values():
            extra[t.name] = t  # agent-local tools win over session extensions

    allow = active_agent.definition.tools_allow if active_agent else None
    deny = active_agent.definition.tools_deny if active_agent else []
    # The agent's own local tools are exempt from its allow/deny filters:
    # they were explicitly contributed by the agent, not pulled from the
    # base pool. This lets a profile ship local tools while still
    # restricting the built-in set.
    always_keep = set(active_agent.local_tools.keys()) if active_agent else set()

    return _filter(base_tool_names, base_tool_pool, extra, allow, deny, always_keep=always_keep)


def compose_active_commands(*, base_commands: dict, active_agent: LoadedAgent | None) -> dict:
    """Compute the active slash-command dict for the runtime.

    Local commands are wrapped in an :class:`ExtensionCommand` so the
    TUI can show the owner consistently with session commands.

    ``ExtensionCommand`` is imported lazily here to break the
    ``vtx.tools`` -> ``vtx.agents`` -> ``vtx.agents.activate`` ->
    ``vtx.extensions`` -> ``vtx.tools`` cycle at module load.
    """
    from ..extensions import ExtensionCommand

    if active_agent is None:
        return dict(base_commands)
    merged = dict(base_commands)
    for name, handler in active_agent.local_commands.items():
        merged[name] = ExtensionCommand(
            name=name,
            description=f"(agent: {active_agent.definition.name}) {name}",
            handler=handler,
            owner=active_agent.definition.name,
        )
    return merged


def active_permission_mode(registry: AgentRegistry, default: str) -> str:
    """Return the active agent's ``permission_mode`` (or the default)."""
    if registry.active is not None and registry.active.definition.permission_mode is not None:
        return registry.active.definition.permission_mode
    return default


def active_permission_gates(registry: AgentRegistry) -> list:
    """Return the union of declarative + imperative gates for the active agent."""
    if registry.active is None:
        return []
    out = list(registry.active.definition.permission_gates)
    for gates in registry.active.local_gates.values():
        out.extend(gates)
    return out


__all__ = [
    "active_permission_gates",
    "active_permission_mode",
    "compose_active_commands",
    "compose_active_tools",
]

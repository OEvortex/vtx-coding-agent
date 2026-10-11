"""The typed service façades that the rest of vtx becomes swappable behind.

Each service here is a thin, typed wrapper over an existing vtx subsystem. The
subsystems keep their implementations; the services are what a plugin registers
on ``ctx.<key>`` and what a consumer reads. That is the whole trick: swapping
the agent loop becomes ``ctx.provide("agentLoop", MyLoop())`` instead of a
rewrite, and no consumer has to change.

Nothing here is required for the kernel to work. A plugin may register anything
under any key.
"""

from __future__ import annotations

import builtins
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from vtx.plugins.errors import ServiceMissingError

# --------------------------------------------------------------------------
# ctx.tools
# --------------------------------------------------------------------------


class ToolService:
    """``ctx.tools`` — the tool set this composition declares.

    Registration reaches the global registry in :mod:`vtx.agent.tools`, so every
    existing consumer keeps working unchanged. Lookup goes through this service's
    own table instead, which is what makes a row authoritative: removing the
    ``tool-grep`` row removes ``grep`` from *this* composition rather than merely
    from the default list.
    """

    def __init__(self) -> None:
        self._owned: dict[str, Any] = {}
        self._defaults: list[str] = []
        self._parent_only: set[str] = set()

    def register(
        self,
        tool: Any,
        *,
        is_default: bool = False,
        parent_only: bool = False,
        override: bool = True,
    ) -> Any:
        """Register a tool instance or class; returns the instance."""
        from vtx.agent.tools import register_tool

        instance = register_tool(
            tool, is_default=is_default, parent_only=parent_only, override=override
        )
        self._owned[instance.name] = instance
        if is_default and instance.name not in self._defaults:
            self._defaults.append(instance.name)
        if parent_only:
            self._parent_only.add(instance.name)
        return instance

    def register_all(self, tools: Sequence[Any], **kwargs: Any) -> list[Any]:
        return [self.register(tool, **kwargs) for tool in tools]

    def unregister(self, name: str) -> None:
        """Remove a tool from this composition **and** the global registry.

        Explicit and rare: most teardown is a row unloading, which uses
        :meth:`forget`.
        """
        from vtx.agent.tools import unregister_tool

        self._forget(name)
        unregister_tool(name)

    def forget(self, name: str) -> None:
        """Drop a tool from this composition's view, leaving the global registry
        alone.

        This is what a row's teardown calls. The global registry is a superset
        that import side effects populate and legacy consumers read, so a row
        unloading must not erase state it did not create.
        """
        self._forget(name)

    def _forget(self, name: str) -> None:
        self._owned.pop(name, None)
        if name in self._defaults:
            self._defaults.remove(name)
        self._parent_only.discard(name)

    def reset(self) -> None:
        """Drop everything this composition registered. Wired as the ``tools``
        row's teardown, so unmounting the row empties the service."""
        for name in list(self._owned):
            self._forget(name)

    def get(self, name: str) -> Any | None:
        return self._owned.get(name)

    def all(self) -> dict[str, Any]:
        return dict(self._owned)

    def names(self) -> list[str]:
        return sorted(self._owned)

    def defaults(self) -> list[str]:
        return list(self._defaults)

    def set_defaults(self, names: Sequence[str]) -> None:
        """Replace the default tool set for this composition."""
        self._defaults = list(names)

    def parent_only(self) -> set[str]:
        return set(self._parent_only)

    def assemble(
        self, base: Sequence[str] | None = None, extras: Sequence[Any] | None = None
    ) -> list[Any]:
        """Build the active list from ``base`` names plus any extras, in order."""
        target = list(base) if base is not None else self.defaults()
        chosen: list[Any] = []
        for name in target:
            tool = self.get(name)
            if tool is not None and tool not in chosen:
                chosen.append(tool)
        if extras:
            # An extension may ship a tool whose name matches a built-in to
            # replace it; dedupe on identity would silently keep the built-in.
            ext_names = {tool.name for tool in extras}
            chosen = [tool for tool in chosen if tool.name not in ext_names]
            for ext_tool in extras:
                if not any(tool.name == ext_tool.name for tool in chosen):
                    chosen.append(ext_tool)
        return chosen


# --------------------------------------------------------------------------
# ctx.llm
# --------------------------------------------------------------------------


class LLMService:
    """``ctx.llm`` — the provider-adapter seam.

    ``create`` is the only required call. A plugin that adds a provider either
    registers it in :mod:`vtx.ai` or wraps this service and routes itself.
    """

    def __init__(self, factory: Callable[..., Any] | None = None) -> None:
        self._factory = factory

    def create(self, api_type: Any, config: Any) -> Any:
        """Instantiate a provider for ``api_type`` from a ``ProviderConfig``."""
        factory = self._factory
        if factory is None:
            from vtx.agent.runtime import create_provider

            factory = create_provider
        return factory(api_type, config)

    def api_type(self, provider: str | None) -> Any:
        """Resolve the API type name for a provider id."""
        from vtx.ai import resolve_provider_api_type

        return resolve_provider_api_type(provider)

    def model(self, provider: str | None, model_id: str | None) -> Any:
        from vtx.ai import get_model

        return get_model(model_id or "", provider)


# --------------------------------------------------------------------------
# ctx.sessions
# --------------------------------------------------------------------------


class SessionService:
    """``ctx.sessions`` — the durable session log."""

    def generate_id(self) -> str:
        from vtx.agent.session import Session

        return Session.generate_id()

    def dir(self, cwd: str) -> Any:
        from vtx.agent.session import Session

        return Session.get_sessions_dir(cwd)

    def create(self, cwd: str, **kwargs: Any) -> Any:
        from vtx.agent.session import Session

        return Session(session_id=Session.generate_id(), cwd=cwd, **kwargs)

    def load(self, path: str) -> Any:
        from vtx.agent.session import Session

        return Session.load(path)

    def list(self, cwd: str) -> builtins.list[Any]:
        from vtx.agent.session import Session

        return Session.list(cwd)


# --------------------------------------------------------------------------
# ctx.permissions
# --------------------------------------------------------------------------


class PermissionService:
    """``ctx.permissions`` — the mutating-tool gate.

    The base decision comes from :func:`vtx.core.permissions.check_permission`.
    Plugins contribute rules that can only ever *tighten* the decision: a rule
    returning ``"prompt"`` or ``"deny"`` escalates, and nothing downgrades to
    ``allow`` below the base decision.
    """

    def __init__(self) -> None:
        self._rules: list[Callable[[str, dict[str, Any]], str | None]] = []

    def add_rule(self, rule: Callable[[str, dict[str, Any]], str | None]) -> Callable[..., Any]:
        """Contribute a ``(tool_name, arguments) -> "allow" | "prompt" | "deny" | None`` rule."""
        self._rules.append(rule)
        return rule

    def decide(self, tool: Any, arguments: dict[str, Any], config: Any = None) -> str:
        from vtx.core.permissions import PermissionDecision, check_permission

        base = check_permission(tool, arguments, config)
        outcome = "allow" if base is PermissionDecision.ALLOW else "prompt"
        for rule in self._rules:
            verdict = rule(getattr(tool, "name", ""), arguments)
            if verdict is None:
                continue
            if verdict == "allow":
                continue
            if verdict == "deny":
                return "deny"
            outcome = "prompt"
        return outcome


# --------------------------------------------------------------------------
# ctx.prompts
# --------------------------------------------------------------------------


class PromptService:
    """``ctx.prompts`` — system-prompt section assembly.

    Sections are registered in order; ``build`` composes the base identity,
    every registered section, and the tool/environment blocks in one string.
    """

    def __init__(self) -> None:
        self._sections: list[tuple[str, Callable[..., str | None]]] = []

    def add_section(
        self, key: str, render: Callable[..., str | None], *, prepend: bool = False
    ) -> Callable[..., str | None]:
        """Register a ``render(cwd, tools) -> str | None`` prompt section."""
        entry = (key, render)
        if prepend:
            self._sections.insert(0, entry)
        else:
            self._sections.append(entry)
        return render

    def section_keys(self) -> list[str]:
        return [key for key, _ in self._sections]

    def build(self, cwd: str, tools: Sequence[Any] | None = None, **kwargs: Any) -> str:
        """Compose the system prompt from this service's registered sections.

        Sections render in registration order and are joined ahead of whatever
        ``extra_instructions`` the caller supplied, so an active agent's profile
        lands after the shared body rather than replacing it.
        """
        from vtx.agent.prompts import build_system_prompt

        caller_extra = kwargs.pop("extra_instructions", None)
        mode = kwargs.pop("extra_instructions_mode", "append")
        base: str | None = kwargs.pop("base_content", None)
        # Sections own context discovery; the caller's loaded context is not
        # forwarded, or it would be bound twice.
        kwargs.pop("context", None)
        parts: list[str] = []
        for _key, render in self._sections:
            try:
                text = render(cwd, tools)
            except Exception:
                text = None
            if text:
                parts.append(text)
        if caller_extra:
            parts.append(caller_extra)
        extra = "\n\n".join(parts)
        return build_system_prompt(
            cwd,
            None,
            list(tools) if tools is not None else None,
            base_content=base,
            extra_instructions=extra or None,
            extra_instructions_mode=mode,
            **kwargs,
        )


# --------------------------------------------------------------------------
# ctx.compaction
# --------------------------------------------------------------------------


class CompactionService:
    """``ctx.compaction`` — overflow detection and summarisation."""

    def is_overflow(self, usage: Any, context_window: int, threshold_percent: float) -> bool:
        from vtx.core.compaction import is_overflow

        return is_overflow(usage, context_window, threshold_percent)

    def summarize(self, *args: Any, **kwargs: Any) -> Any:
        from vtx.core.compaction import generate_summary

        return generate_summary(*args, **kwargs)


# --------------------------------------------------------------------------
# ctx.sessionProjections
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Projection:
    """One fold over the session event stream.

    ``apply`` returns the new state for the event it was given and must return
    the *same* state object when the event is irrelevant, so a consumer holding
    a reference can tell whether anything changed.
    """

    key: str
    init: Callable[[], Any]
    apply: Callable[[Any, Any], Any]
    state_schema: Any = None
    wire: Any = None
    state_version: int = 1


class ProjectionService:
    """``ctx.sessionProjections`` — incremental folds over committed events.

    The rule that makes this load-bearing: **anything the model can see must be
    reconstructable from the log.** A projection is how a UI, a panel, or a
    plugin reads that log back as typed state without re-deriving it, and it
    never invents a value that is not in an event.

    Folding is incremental: each key keeps a cursor, so handing a growing event
    list applies only the events added since the last call.
    """

    def __init__(self) -> None:
        self._units: dict[str, Projection] = {}
        self._cursors: dict[str, list[Any]] = {}
        self._states: dict[str, Any] = {}

    def register(self, projection: Projection) -> Projection:
        if not projection.key:
            raise ValueError("a projection needs a non-empty key")
        self._units[projection.key] = projection
        # A registration replaces any folded state: the definition changed.
        self._cursors.pop(projection.key, None)
        self._states.pop(projection.key, None)
        return projection

    def keys(self) -> list[str]:
        return sorted(self._units)

    def definitions(self) -> dict[str, Projection]:
        return dict(self._units)

    def _fold(self, key: str, events: Sequence[Any]) -> Any:
        unit = self._units[key]
        cursor = self._cursors.setdefault(key, [])
        if key not in self._states:
            self._states[key] = unit.init()
        state = self._states[key]
        for event in list(events)[len(cursor) :]:
            state = unit.apply(state, event)
        cursor.extend(list(events)[len(cursor) :])
        self._states[key] = state
        return state

    def state_of(self, key: str, events: Sequence[Any] = ()) -> Any:
        """The current state for ``key``, folding anything not yet consumed."""
        if key not in self._units:
            raise ServiceMissingError(f"no projection is registered under {key!r}")
        return self._fold(key, events)

    def snapshot(
        self, keys: Sequence[str] | None = None, events: Sequence[Any] = ()
    ) -> dict[str, Any]:
        """Every requested projection's state in one call."""
        targets = list(keys) if keys is not None else self.keys()
        return {key: self.state_of(key, events) for key in targets}

    def reset(self) -> None:
        """Forget all folded state, keeping the registered definitions."""
        self._cursors.clear()
        self._states.clear()


# --------------------------------------------------------------------------
# ctx.goals
# --------------------------------------------------------------------------


class GoalService:
    """``ctx.goals`` — the same-session objective and its lifecycle.

    A goal is one objective for the whole session, not per agent: a sub-agent
    inherits its parent's, so the service hands out the single active record
    rather than one per caller.
    """

    def __init__(self) -> None:
        self._records: dict[str, Any] = {}
        self._active: str | None = None

    def set(self, session_id: str, record: Any) -> Any:
        self._records[session_id] = record
        self._active = session_id
        return record

    def get(self, session_id: str) -> Any | None:
        return self._records.get(session_id)

    def clear(self, session_id: str) -> bool:
        if self._active == session_id:
            self._active = None
        return self._records.pop(session_id, None) is not None

    @property
    def active(self) -> Any | None:
        return self._records.get(self._active) if self._active else None

    def sessions(self) -> list[str]:
        return sorted(self._records)


# --------------------------------------------------------------------------
# ctx.skills
# --------------------------------------------------------------------------


class SkillService:
    """``ctx.skills`` — the skill catalog a prompt indexes."""

    def __init__(self) -> None:
        self._extra: list[Any] = []

    def add(self, skill: Any) -> Any:
        self._extra.append(skill)
        return skill

    def all(self) -> list[Any]:
        return list(self._extra)


# --------------------------------------------------------------------------
# ctx.telemetry
# --------------------------------------------------------------------------


class TelemetryService:
    """``ctx.telemetry`` — the span sink every package reports through."""

    def __init__(self) -> None:
        self._exporters: list[Any] = []

    def add_exporter(self, exporter: Any) -> Any:
        self._exporters.append(exporter)
        return exporter

    @property
    def exporters(self) -> list[Any]:
        return list(self._exporters)


# --------------------------------------------------------------------------
# ctx.sandbox
# --------------------------------------------------------------------------


class SandboxService:
    """``ctx.sandbox`` — confined script execution (the codemode worker).

    There is no default runner: the composition's sandbox provider installs one,
    which is the point of the seam. Calling :meth:`run` without one raises
    rather than falling back to something the operator did not choose.
    """

    def __init__(self, runner: Any = None) -> None:
        self.runner = runner

    def set_runner(self, runner: Any) -> Any:
        self.runner = runner
        return runner

    def run(self, script: str, *args: Any, **kwargs: Any) -> Any:
        runner = self.runner
        if runner is None:
            raise ServiceMissingError(
                "no sandbox runner is installed; a provider must call "
                "ctx.sandbox.set_runner(...) or provide its own ctx.sandbox"
            )
        return runner(script, *args, **kwargs)


# --------------------------------------------------------------------------
# ctx.mcp
# --------------------------------------------------------------------------


class MCPService:
    """``ctx.mcp`` — external servers and the tools they expose.

    The manager itself is a provider: a composition that hands ``set_manager``
    a different one gets a different set of external tools, and a row's teardown
    unwinds the tools it contributed.
    """

    def __init__(self) -> None:
        self._manager: Any = None
        self._tools: list[Any] = []

    @property
    def manager(self) -> Any:
        return self._manager

    def set_manager(self, manager: Any) -> Any:
        self._manager = manager
        return manager

    def connect(self, *, cwd: str, project_trusted: bool = False) -> Any:
        """Build and attach the manager described by the loaded MCP config.

        Returns the manager, or ``None`` when this machine declares no servers.
        """
        if self._manager is not None:
            return self._manager
        from vtx.mcp.manager import McpManager

        manager = McpManager(cwd=cwd, project_trusted=project_trusted)
        self._manager = manager
        return manager

    def expose(self, tools: Sequence[Any]) -> list[Any]:
        """Record the tools one manager handed back, so an unload can retract.

        Exposure is decided by each tool's own declared value; the service does
        not second-guess it.
        """
        accepted = list(tools)
        self._tools.extend(accepted)
        return accepted

    def tools(self) -> list[Any]:
        """The external tools this composition currently has."""
        return list(self._tools)

    def reset(self) -> None:
        """Drop the exposed tools. Wired as the ``mcp`` row's teardown."""
        self._tools.clear()


# --------------------------------------------------------------------------
# ctx.agents
# --------------------------------------------------------------------------


class AgentService:
    """``ctx.agents`` — agent profiles and the active selection."""

    def __init__(self, registry: Any = None) -> None:
        self._registry = registry

    @property
    def registry(self) -> Any:
        if self._registry is None:
            from vtx.agent.agents import AgentRegistry

            self._registry = AgentRegistry()
        return self._registry

    def names(self) -> list[str]:
        return list(self.registry.names)

    def active(self) -> Any:
        return self.registry.active

    def set_active(self, name: str | None) -> Any:
        return self.registry.set_active(name)


__all__ = [
    "AgentService",
    "CompactionService",
    "GoalService",
    "LLMService",
    "MCPService",
    "PermissionService",
    "Projection",
    "ProjectionService",
    "PromptService",
    "SandboxService",
    "SessionService",
    "SkillService",
    "TelemetryService",
    "ToolService",
]

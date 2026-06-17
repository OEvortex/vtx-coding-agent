"""``AgentAPI`` — the object passed to an agent file's ``register(api)`` hook.

Mirrors :class:`vtx.extensions.ExtensionAPI` so an agent file feels like
an extension scoped to a single agent. Tools and commands registered here
are only visible when this agent is the active one.
"""

from __future__ import annotations

import sys
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from ..extensions import CommandOutcome, ExtensionTool, _json_schema_to_pydantic
from ..tools.base import BaseTool
from .schema import AgentDef, PermissionAction, PermissionGate

NotifyLevel = Literal["info", "warning", "error"]


@dataclass
class LoadedAgent:
    """A loaded agent plus everything its file contributed."""

    definition: AgentDef
    path: Path
    # Tools registered via ``api.local_tool``. Keyed by tool name. Scoped to
    # this agent: only present in the active tool set when this agent is active.
    local_tools: dict[str, BaseTool] = field(default_factory=dict)
    # Slash commands registered via ``api.local_command``. Keyed by command name
    # without the leading ``/``. Scoped to this agent. The value is the wrapped
    # ``Callable[[str], CommandOutcome]`` handler (mirrors
    # :class:`vtx.extensions.ExtensionCommand.handler`).
    local_commands: dict[str, Callable[[str], CommandOutcome]] = field(default_factory=dict)
    # Extra permission gates registered via ``api.permission_gate``. Keyed
    # by tool name. Layered on top of ``AgentDef.permission_gates``.
    local_gates: dict[str, list[PermissionGate]] = field(default_factory=dict)
    # Event handlers keyed by event name. Use ``api.on(event, handler)``.
    handlers: dict[str, list[Callable[..., Any]]] = field(default_factory=dict)
    # Load-time error (if any); surfaced in startup warnings.
    error: str | None = None

    @property
    def name(self) -> str:
        return self.definition.name

    def wire_handlers(self, bus) -> None:
        """Register every stored handler with the given event bus.

        The runtime calls this when the agent is activated so that the
        agent's ``@api.on(...)`` registrations participate in the
        lifecycle event stream. The bus's ``on()`` method appends to its
        internal list, so this is additive and idempotent within a single
        bus instance.
        """
        for event, handlers in self.handlers.items():
            for h in handlers:
                bus.on(event, h)


def _when_predicate(when: str) -> Callable[[dict[str, Any]], bool]:
    """Compile a small ``when`` expression into a predicate over args.

    Supported grammar (kept tiny on purpose):

        <path> matches '<literal>'
        <path> == '<literal>'

    ``<path>`` is a dotted path into the args dict. Unknown expressions
    raise ``ValueError`` at registration time so the user sees the error
    at startup, not at runtime.
    """
    s = when.strip()
    if " matches " in s:
        path, literal = s.split(" matches ", 1)
        op = "matches"
    elif " == " in s:
        path, literal = s.split(" == ", 1)
        op = "eq"
    else:
        raise ValueError(
            f"unsupported 'when' expression: {when!r}. "
            "Use '<path> matches \"<literal>\"' or '<path> == \"<literal>\"'."
        )

    path = path.strip()
    literal = literal.strip()
    if not (literal.startswith("'") and literal.endswith("'")) and not (
        literal.startswith('"') and literal.endswith('"')
    ):
        raise ValueError(f"literal in 'when' must be quoted: {when!r}")
    literal_value = literal[1:-1]

    def _lookup(args: dict[str, Any]) -> Any:
        cur: Any = args
        for part in path.split("."):
            if not part:
                continue
            if isinstance(cur, dict):
                cur = cur.get(part)
            else:
                return None
        return cur

    if op == "matches":
        return lambda args: literal_value in str(_lookup(args) or "")
    return lambda args: str(_lookup(args) or "") == literal_value


class AgentAPI:
    """Object passed to an agent file's ``register(api)`` function.

    Methods are intentionally additive over :class:`vtx.extensions.ExtensionAPI`:
    they share the same event-bus semantics, the same JSON-Schema -> pydantic
    path for tool params, and the same ``CommandOutcome`` shape for commands.
    """

    def __init__(
        self,
        loaded: LoadedAgent,
        *,
        cwd: str,
        config_dir: Path,
        on_event: Callable[[str, Callable[..., Any]], None] | None = None,
    ) -> None:
        self._loaded = loaded
        self._cwd = cwd
        self._config_dir = config_dir
        self._on_event = on_event

    # ---- read-only context ----------------------------------------------

    @property
    def name(self) -> str:
        return self._loaded.definition.name

    @property
    def definition(self) -> AgentDef:
        return self._loaded.definition

    @property
    def cwd(self) -> str:
        return self._cwd

    @property
    def config_dir(self) -> Path:
        return self._config_dir

    # ---- agent-scoped tools ---------------------------------------------

    def local_tool(
        self,
        name: str,
        description: str | None = None,
        parameters: dict[str, Any] | None = None,
        *,
        execute: Callable[[dict[str, Any], dict[str, Any] | None], Any] | None = None,
        mutating: bool = True,
        label: str | None = None,
    ) -> Any:
        """Register a tool that exists only when this agent is active.

        Two call styles are supported::

            # Function call: explicit ``execute=`` callback
            api.local_tool(
                name="pr_summary",
                description="Summarize the PR",
                parameters={...},
                execute=lambda args, ctx: {...},
                mutating=False,
            )

            # Decorator: the function below is the execute callback
            @api.local_tool(
                name="pr_summary",
                description="Summarize the PR",
                parameters={...},
            )
            def pr_summary(args, ctx):
                return {...}

        Same semantics as ``ExtensionAPI.register_tool``, but stored on
        :class:`LoadedAgent` and surfaced only when this agent is the
        active one in the runtime.
        """
        if not name or not isinstance(name, str):
            raise ValueError("Tool name must be a non-empty string")

        # Decorator form: the user wrote ``@api.local_tool(name="x", ...)``
        # and the function below is the execute callback. We detect this
        # by the absence of an explicit ``execute=`` argument; everything
        # else is taken from the decorator.
        if execute is None and description is not None and parameters is not None:

            def _decorator(fn: Callable[..., Any]) -> BaseTool:
                params_model = _json_schema_to_pydantic(name, parameters)
                tool = ExtensionTool(
                    name=name,
                    description=description,
                    parameters=parameters,
                    params_model=params_model,
                    execute_fn=fn,
                    owner=self._loaded.definition.name,
                    mutating=mutating,
                    label=label or name,
                )
                self._loaded.local_tools[name] = tool
                return tool

            return _decorator

        if description is None or parameters is None or execute is None:
            raise ValueError(
                "description, parameters, and execute are required for local_tool "
                "(or use the @api.local_tool(...) decorator form)"
            )

        params_model = _json_schema_to_pydantic(name, parameters)
        tool = ExtensionTool(
            name=name,
            description=description,
            parameters=parameters,
            params_model=params_model,
            execute_fn=execute,
            owner=self._loaded.definition.name,
            mutating=mutating,
            label=label or name,
        )
        self._loaded.local_tools[name] = tool
        return tool

    # ---- agent-scoped slash commands ------------------------------------

    def local_command(
        self,
        name: str,
        description: str | None = None,
        handler: Callable[[str], CommandOutcome | str | None] | None = None,
    ) -> Any:
        """Register a slash command scoped to this agent.

        Two call styles are supported::

            # Function call
            api.local_command(
                name="checklist",
                description="Run the checklist",
                handler=lambda args: "result",
            )

            # Decorator
            @api.local_command(name="checklist", description="Run the checklist")
            def checklist(args):
                return "result"

        ``handler`` receives the argument string (everything after ``/name``)
        and may return a :class:`CommandOutcome`, a string (treated as
        ``output``), or ``None`` (silently succeeded). The wrapper records
        the owner so the UI can show the source of the command.
        """
        if not name or not isinstance(name, str):
            raise ValueError("Command name must be a non-empty string")

        cleaned_name = name.lstrip("/") if name.startswith("/") else name

        def _wrap(
            fn: Callable[[str], CommandOutcome | str | None],
        ) -> Callable[[str], CommandOutcome]:
            def _wrapper(args: str) -> CommandOutcome:
                try:
                    result = fn(args)
                except Exception:
                    traceback.print_exc(file=sys.stderr)
                    return CommandOutcome(output="(command raised an error)", success=False)
                if result is None:
                    return CommandOutcome(output="")
                if isinstance(result, CommandOutcome):
                    return result
                return CommandOutcome(output=str(result))

            return _wrapper

        # Decorator form: handler is None, description provided.
        if handler is None and description is not None:

            def _decorator(
                fn: Callable[[str], CommandOutcome | str | None],
            ) -> Callable[[str], CommandOutcome]:
                wrapped = _wrap(fn)
                self._loaded.local_commands[cleaned_name] = wrapped
                return wrapped

            return _decorator

        if handler is None:
            raise ValueError(
                "handler is required for local_command "
                "(or use the @api.local_command(...) decorator form)"
            )
        if description is None:
            raise ValueError("description is required for local_command")

        wrapped = _wrap(handler)
        self._loaded.local_commands[cleaned_name] = wrapped
        return wrapped

    # ---- agent-scoped permission gates ----------------------------------

    def permission_gate(
        self,
        tool: str,
        *,
        when: str | Callable[[dict[str, Any]], bool],
        action: PermissionAction,
        reason: str | None = None,
    ) -> None:
        """Register a permission rule scoped to this agent.

        ``when`` may be a small expression string (see :class:`PermissionGate`
        in :mod:`vtx.agents.schema`) or a Python callable that takes the
        tool args dict and returns ``True`` to apply the rule.
        """
        if not tool or not isinstance(tool, str):
            raise ValueError("Tool name must be a non-empty string")
        if action not in ("allow", "deny", "prompt"):
            raise ValueError(f"invalid action: {action!r}")
        predicate = _when_predicate(when) if isinstance(when, str) else when
        gate = PermissionGate(tool=tool, when="<callable>", action=action, reason=reason)
        # Stash the compiled predicate on the gate so the permissions layer
        # can call it. PermissionGate is otherwise a frozen Pydantic model;
        # we attach the predicate via a private attribute.
        object.__setattr__(gate, "_predicate", predicate)
        self._loaded.local_gates.setdefault(tool, []).append(gate)

    # ---- events ---------------------------------------------------------

    def on(self, event: str, handler: Callable[..., Any] | None = None) -> Any:
        """Subscribe ``handler`` to a lifecycle event for this agent.

        Mirrors :meth:`vtx.extensions.ExtensionAPI.on` exactly. Supports
        both ``api.on(EVENT, handler)`` and ``@api.on(EVENT)`` forms.
        """
        from ..extensions import ALL_EVENTS

        if event not in ALL_EVENTS:
            raise ValueError(f"Unknown event {event!r}. Valid events: {', '.join(ALL_EVENTS)}")
        if handler is None:

            def _decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
                self._loaded.handlers.setdefault(event, []).append(fn)
                if self._on_event is not None:
                    self._on_event(event, fn)
                return fn

            return _decorator

        self._loaded.handlers.setdefault(event, []).append(handler)
        if self._on_event is not None:
            self._on_event(event, handler)
        return handler

    def on_agent_change(self, handler: Callable[..., Any] | None = None) -> Any:
        """Shortcut for ``api.on('agent_changed', handler)``."""
        from ..extensions import AGENT_CHANGED

        return self.on(AGENT_CHANGED, handler)

    # ---- notifications --------------------------------------------------

    def notify(self, message: str, level: NotifyLevel = "info") -> None:
        """Emit a user-facing notification from the agent file.

        Prints to stderr (the chat log surfaces stderr lines).
        """
        prefix = {"info": "[agent]", "warning": "[agent:warn]", "error": "[agent:error]"}.get(
            level, "[agent]"
        )
        print(f"{prefix} {self._loaded.definition.name}: {message}", file=sys.stderr)


__all__ = ["AgentAPI", "LoadedAgent"]

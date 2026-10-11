"""The ``register(api)`` surface, implemented over the plugin context.

An extension module stays exactly what it was: a file with a top-level
``register(api)`` callable. What changes is what ``api`` is — no longer an object
with its own registry and its own event bus, but a view onto the composition's
``ctx``. Every registration an extension makes therefore lands in the one place
the composition owns, so a plugin row and an extension file are peers.

    # .vtx/extensions/my_tool.py
    def register(api):
        api.register_tool("greet", "...", {...}, execute=greet)
        api.on("session_start", lambda event, payload: print(event))
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from vtx.agent.tools.base import BaseTool
from vtx.core.paths import get_config_dir
from vtx.plugins.builtins.tools._register import mount_tool
from vtx.plugins.event_contract import (
    AGENT_ACTIVATED,
    AGENT_CHANGED,
    AGENT_END,
    AGENT_START,
    ALL_EVENTS,
    BEFORE_AGENT_START,
    BEFORE_PROVIDER_HEADERS,
    BEFORE_PROVIDER_REQUEST,
    BLOCK,
    INPUT,
    MODEL_SELECT,
    REASON,
    SESSION_END,
    SESSION_START,
    THINKING_LEVEL_SELECT,
    TOOL_CALL,
    TOOL_GROUP_CHANGED,
    TOOL_RESULT,
    TURN_END,
    TURN_START,
    USER_BASH,
    build_event_object,
)
from vtx.protocol.types import ToolResult

log = logging.getLogger("vtx.plugins.api")


@dataclass
class ExtensionShortcut:
    shortcut: str
    description: str | None = None
    handler: Callable[..., Any] | None = None
    extension_path: str = ""


@dataclass
class CommandOutcome:
    """What a slash command returns to the runtime."""

    output: str = ""
    success: bool = True
    exit_after: bool = False  # True for a command that should end the session


@dataclass
class ExtensionCommand:
    """A slash command an extension or an agent registered."""

    name: str
    description: str = ""
    handler: Callable[..., Any] | None = None
    owner: str = ""
    #: Whether the command's argument is passed raw rather than as one string.
    submit_on_select: bool = True


@dataclass
class ExtensionFlag:
    name: str
    description: str | None = None
    type: str = "boolean"
    default: Any = None
    extension_path: str = ""


@dataclass
class ExtensionProviderRegistration:
    name: str
    config: dict[str, Any]


@dataclass
class ExtensionNativeProviderRegistration:
    provider: Any


@dataclass
class ExtensionError:
    extension_path: str
    event: str
    error: str
    stack: str | None = None


class ExtensionRecord:
    """What one loaded extension contributed, for ``/extensions`` and ``--list-extensions``."""

    def __init__(self, name: str, path: Path) -> None:
        self.name = name
        self.path = path
        self.tools: dict[str, Any] = {}
        self.commands: dict[str, Any] = {}
        self.local_tools: dict[str, dict[str, Any]] = {}
        self.shortcuts: dict[str, ExtensionShortcut] = {}
        self.flags: dict[str, ExtensionFlag] = {}
        self.message_renderers: dict[str, Callable[..., Any]] = {}
        self.entry_renderers: dict[str, Callable[..., Any]] = {}
        self.handlers: dict[str, list[Callable[..., Any]]] = {}
        self.handler_calls: dict[str, int] = {}

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": str(self.path),
            "tools": sorted(self.tools),
            "commands": sorted(self.commands),
            "handlers": sorted(self.handler_calls),
        }


class ExtensionActions:
    """The capabilities a host binds so an extension can act on the session.

    Every method is a no-op until the host installs an implementation, which is
    what makes an extension loadable in a runtime that has no session yet.
    """

    def __init__(self) -> None:
        self._impl: dict[str, Callable[..., Any]] = {}

    def bind(self, name: str, fn: Callable[..., Any]) -> None:
        self._impl[name] = fn

    def bind_many(self, **kwargs: Any) -> None:
        """Bind several actions at once, by the name a handler calls them by."""
        for name, fn in kwargs.items():
            self.bind(name, fn)

    def call(self, name: str, *args: Any, **kwargs: Any) -> Any:
        fn = self._impl.get(name)
        return fn(*args, **kwargs) if fn is not None else None


def _qualname(fn: Any) -> str:
    module = getattr(fn, "__module__", "")
    name = getattr(fn, "__qualname__", None) or getattr(fn, "__name__", None) or repr(fn)
    return f"{module}.{name}" if module else str(name)


class ExtensionAPI:
    """The object handed to an extension's ``register`` function.

    ``ctx`` is the extension's own plugin scope: registering through it means the
    row's teardown unwinds the registration, and dropping ``ctx`` is never
    needed because the composition owns it.
    """

    def __init__(
        self,
        name: str,
        ctx: Any,
        record: ExtensionRecord,
        actions: ExtensionActions,
        *,
        cwd: str = "",
        session_file: str | None = None,
    ) -> None:
        self.name = name
        self._ctx = ctx
        self._record = record
        self._actions = actions
        self.cwd = cwd
        self.session_file = session_file
        self.config_dir = get_config_dir()

    # ---- events ----------------------------------------------------------

    def on(self, event: str, handler: Callable[..., Any] | None = None) -> Any:
        """Subscribe ``handler`` to ``event``.

        Two call forms are supported::

            api.on(TOOL_CALL, handler)
            @api.on(TOOL_CALL)
            def handler(event, payload): ...

        A blocking handler may return ``{"block": True, "reason": ...}`` to stop
        the work, or ``{"args": {...}}`` / ``{"output": ...}`` to rewrite it.
        """
        if event not in ALL_EVENTS:
            raise ValueError(f"Unknown event {event!r}. Valid events: {', '.join(ALL_EVENTS)}")

        def subscribe(fn: Callable[..., Any]) -> Callable[..., Any]:
            name = _qualname(fn)

            def wrapped(payload: Any) -> Any:
                self._record.handler_calls[name] = self._record.handler_calls.get(name, 0) + 1
                # A handler written ``(event, payload)`` or ``(event, payload,
                # ctx)`` keeps working; the hub's convention is one value.
                built = build_event_object(event, dict(payload))
                if _accepts_third(fn):
                    return fn(event, built, self)
                return fn(event, built)

            self._ctx.on(event, wrapped)
            return fn

        if handler is None:
            return subscribe
        return subscribe(handler)

    # ---- tools -----------------------------------------------------------

    def register_tool(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        *,
        execute: Callable[[dict[str, Any], dict[str, Any] | None], Any],
        mutating: bool = True,
        label: str | None = None,
        ui_block: type | None = None,
        tool_icon: str | None = None,
    ) -> Any:
        """Register an LLM-callable tool. Overriding a built-in replaces it."""
        if not name:
            raise ValueError("Tool name must be a non-empty string")
        from vtx.agent.tools.schema import json_schema_to_pydantic

        tool = ExtensionTool(
            name=name,
            description=description,
            parameters=parameters,
            params_model=json_schema_to_pydantic(name, parameters),
            execute_fn=execute,
            owner=self.name,
            mutating=mutating,
            label=label or name,
            ui_block=ui_block,
        )
        if tool_icon is not None:
            tool.tool_icon = tool_icon
        self._record.tools[name] = tool
        return mount_tool(self._ctx, tool)

    def register_local_tool(
        self,
        agent: str,
        name: str,
        description: str,
        parameters: dict[str, Any],
        *,
        execute: Callable[[dict[str, Any], dict[str, Any] | None], Any],
        mutating: bool = True,
        label: str | None = None,
        ui_block: type | None = None,
    ) -> Any:
        """Register a tool scoped to one handoff agent rather than to every session."""
        tool = self.register_tool(
            name,
            description,
            parameters,
            execute=execute,
            mutating=mutating,
            label=label,
            ui_block=ui_block,
        )
        self._record.local_tools.setdefault(agent, {})[name] = tool
        return tool

    def register_command(
        self, name: str, description_or_def: Any, handler: Callable[..., Any] | None = None
    ) -> None:
        """Register a slash command. It dispatches without a model turn.

        Two spellings are accepted, because both are in the wild::

            api.register_command("name", "description", handler)
            api.register_command("name", ExtensionCommand(...))
        """
        if not name:
            raise ValueError("Command name must be non-empty")
        if handler is None and not isinstance(description_or_def, str):
            definition = description_or_def
            handler = getattr(definition, "handler", None)
            description = str(getattr(definition, "description", ""))
        else:
            description = str(description_or_def)
        if handler is None:
            raise ValueError(f"command {name!r} needs a handler")
        # Store the wrapped form, so a caller reading ``command.handler`` gets a
        # CommandOutcome back whichever spelling the extension used.
        wrapped = _as_command(handler)
        command = ExtensionCommand(
            name=name, description=description, handler=wrapped, owner=self.name
        )
        self._record.commands[name] = command
        commands = self._ctx.get("commands")
        if commands is not None:
            commands.register(name, wrapped, description)

    # ---- shortcuts and flags ---------------------------------------------

    def register_shortcut(self, shortcut: str, options: dict[str, Any]) -> None:
        self._record.shortcuts[shortcut] = ExtensionShortcut(
            shortcut=shortcut,
            description=options.get("description"),
            handler=options.get("handler"),
            extension_path=str(getattr(self, "path", "")),
        )

    def register_flag(self, name: str, options: dict[str, Any]) -> None:
        self._record.flags[name] = ExtensionFlag(
            name=name,
            description=options.get("description"),
            type=options.get("type", "boolean"),
            default=options.get("default"),
            extension_path=str(getattr(self, "path", "")),
        )

    def get_flag(self, name: str) -> Any:
        flag = self._record.flags.get(name)
        if flag is None:
            return None
        return self._actions.call("get_flag", name) or flag.default

    # ---- renderers -------------------------------------------------------

    def register_message_renderer(self, custom_type: str, renderer: Callable[..., Any]) -> None:
        self._record.message_renderers[custom_type] = renderer

    def register_entry_renderer(self, custom_type: str, renderer: Callable[..., Any]) -> None:
        self._record.entry_renderers[custom_type] = renderer

    # ---- UI --------------------------------------------------------------

    def notify(self, message: str, level: Literal["info", "warning", "error"] = "info") -> None:
        """Show a user-facing message. Routes through ``ctx.ui`` when installed."""
        ui = self._ctx.get("ui")
        if ui is not None:
            ui.notify(f"{self.name}: {message}", level)
            return
        getattr(log, level if level in ("info", "warning", "error") else "info")(
            "%s: %s", self.name, message
        )

    @property
    def ui(self) -> Any:
        ui = self._ctx.get("ui")
        return ui if ui is not None and getattr(ui, "installed", False) else None

    # ---- session actions -------------------------------------------------

    def send_message(self, message: dict[str, Any], options: dict[str, Any] | None = None) -> None:
        self._actions.call("send_message", message, options)

    def send_user_message(
        self, content: str | list[Any], options: dict[str, Any] | None = None
    ) -> None:
        self._actions.call("send_user_message", content, options)

    def append_entry(self, custom_type: str, data: Any = None) -> None:
        self._actions.call("append_entry", custom_type, data)

    def set_session_name(self, name: str) -> None:
        self._actions.call("set_session_name", name)

    def get_session_name(self) -> str | None:
        return self._actions.call("get_session_name")

    def set_label(self, entry_id: str, label: str | None) -> None:
        self._actions.call("set_label", entry_id, label)

    def exec(self, command: str, args: list[str], options: dict[str, Any] | None = None) -> Any:
        cwd = options.get("cwd") if options else None
        try:
            result = subprocess.run(
                [command, *args],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=(options or {}).get("timeout"),
            )
            return {"code": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
        except Exception as exc:
            return {"code": -1, "stdout": "", "stderr": str(exc)}

    def get_active_tools(self) -> list[str]:
        return self._actions.call("get_active_tools") or []

    def get_all_tools(self) -> list[dict[str, Any]]:
        return self._actions.call("get_all_tools") or []

    def set_active_tools(self, tool_names: list[str]) -> None:
        self._actions.call("set_active_tools", tool_names)

    def set_model(self, model: Any) -> bool:
        return bool(self._actions.call("set_model", model))

    def get_thinking_level(self) -> str | None:
        return self._actions.call("get_thinking_level")

    def set_thinking_level(self, level: str) -> None:
        self._actions.call("set_thinking_level", level)

    # ---- provider registration -------------------------------------------

    def register_provider(
        self, provider_or_name: Any, config: dict[str, Any] | None = None
    ) -> None:
        pending = self._actions.call("pending_providers")
        if pending is None:
            return
        if isinstance(provider_or_name, str):
            pending.append(
                ExtensionProviderRegistration(name=provider_or_name, config=config or {})
            )
        else:
            pending.append(ExtensionNativeProviderRegistration(provider=provider_or_name))

    def unregister_provider(self, name: str) -> None:
        pending = self._actions.call("pending_providers")
        if not pending:
            return
        kept = [
            p
            for p in pending
            if not (isinstance(p, ExtensionProviderRegistration) and p.name == name)
        ]
        pending[:] = kept

    # ---- convenience -----------------------------------------------------

    def on_session_start(self, handler: Callable[..., Any]) -> Callable[..., Any]:
        return self.on(SESSION_START, handler)

    def on_session_end(self, handler: Callable[..., Any]) -> Callable[..., Any]:
        return self.on(SESSION_END, handler)

    def on_agent_start(self, handler: Callable[..., Any]) -> Callable[..., Any]:
        return self.on(AGENT_START, handler)

    def on_agent_end(self, handler: Callable[..., Any]) -> Callable[..., Any]:
        return self.on(AGENT_END, handler)

    def on_turn_start(self, handler: Callable[..., Any]) -> Callable[..., Any]:
        return self.on(TURN_START, handler)

    def on_turn_end(self, handler: Callable[..., Any]) -> Callable[..., Any]:
        return self.on(TURN_END, handler)

    def on_tool_call(self, handler: Callable[..., Any]) -> Callable[..., Any]:
        return self.on(TOOL_CALL, handler)

    def on_tool_result(self, handler: Callable[..., Any]) -> Callable[..., Any]:
        return self.on(TOOL_RESULT, handler)

    def on_user_bash(self, handler: Callable[..., Any]) -> Callable[..., Any]:
        return self.on(USER_BASH, handler)

    def on_input(self, handler: Callable[..., Any]) -> Callable[..., Any]:
        return self.on(INPUT, handler)

    def on_before_agent_start(self, handler: Callable[..., Any]) -> Callable[..., Any]:
        return self.on(BEFORE_AGENT_START, handler)

    def on_agent_activated(self, handler: Callable[..., Any]) -> Callable[..., Any]:
        return self.on(AGENT_ACTIVATED, handler)

    def on_agent_changed(self, handler: Callable[..., Any]) -> Callable[..., Any]:
        return self.on(AGENT_CHANGED, handler)

    def on_tool_group_changed(self, handler: Callable[..., Any]) -> Callable[..., Any]:
        return self.on(TOOL_GROUP_CHANGED, handler)

    def on_model_select(self, handler: Callable[..., Any]) -> Callable[..., Any]:
        return self.on(MODEL_SELECT, handler)

    def on_thinking_level_select(self, handler: Callable[..., Any]) -> Callable[..., Any]:
        return self.on(THINKING_LEVEL_SELECT, handler)

    def on_before_provider_request(self, handler: Callable[..., Any]) -> Callable[..., Any]:
        return self.on(BEFORE_PROVIDER_REQUEST, handler)

    def on_before_provider_headers(self, handler: Callable[..., Any]) -> Callable[..., Any]:
        return self.on(BEFORE_PROVIDER_HEADERS, handler)


def _as_command(handler: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap a command handler so its return is a :class:`CommandOutcome`.

    A handler written ``lambda args: "hi"`` returns a string; one written
    ``lambda args: CommandOutcome(output="hi")`` returns the outcome directly.
    Both are in the wild, so the string is lifted rather than rejected.
    """

    def run(*args: Any, **kwargs: Any) -> CommandOutcome:
        result = handler(*args, **kwargs)
        if isinstance(result, CommandOutcome):
            return result
        return CommandOutcome(output="" if result is None else str(result))

    return run


def _accepts_third(handler: Any) -> bool:
    """Whether ``handler`` declares a third positional parameter.

    Never memoised: a cache keyed on ``id(handler)`` inherits the answer of a
    freed handler once CPython recycles its address, which made a live
    two-argument handler get called with three arguments — and the resulting
    ``TypeError`` was swallowed by dispatch, so the handler just stopped firing.
    """
    import inspect

    try:
        parameters = inspect.signature(handler).parameters.values()
    except (TypeError, ValueError):
        return False
    positional = sum(
        1 for p in parameters if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    )
    return positional >= 3 or any(p.kind is p.VAR_POSITIONAL for p in parameters)


class ExtensionUIContext:
    """The interactive surface an extension sees as ``ctx.ui``.

    The TUI installs :class:`vtx.tui.extension_ui.TextualExtensionUI`; this base
    class provides safe no-op fallbacks so the same handler code runs in every
    mode. The dialog primitives are coroutines, so ``await ctx.ui.confirm(...)``
    works in headless/print mode too — it just resolves to the safe default
    immediately rather than raising.

    Both optional cancellation knobs are honoured by every primitive:

    - ``timeout``: seconds; on expiry the dialog is dismissed with its default.
    - ``signal``: an ``asyncio.Event``-like object whose ``wait()`` resolves the
      dialog with its default, so an abort cancels a hanging prompt.
    """

    async def select(
        self, title: str, options: list[str], *, timeout: float | None = None, signal: Any = None
    ) -> str | None:
        """Ask the person to pick one of ``options``; returns the choice or ``None``."""
        return None

    async def confirm(
        self,
        title: str,
        message: str | None = None,
        *,
        timeout: float | None = None,
        signal: Any = None,
    ) -> bool:
        """Ask a yes/no question; returns ``True`` only if the user confirms."""
        return False

    async def input(
        self,
        title: str,
        placeholder: str = "",
        *,
        default: str | None = None,
        timeout: float | None = None,
        signal: Any = None,
    ) -> str | None:
        """Ask for free-form text; returns the entered string or ``None``."""
        return None

    async def custom(
        self, component: Any, *, timeout: float | None = None, signal: Any = None
    ) -> Any:
        """Show a custom UI component modally; returns whatever it dismisses with."""
        return None

    def notify(self, message: str, level: str = "info") -> None:
        pass

    def setStatus(self, key: str, value: str | None) -> None:  # noqa: N802
        pass

    def setWidget(self, key: str, widget: Any, options: dict[str, Any] | None = None) -> None:  # noqa: N802
        pass

    def setFooter(self, footer: Any) -> None:  # noqa: N802
        pass

    def setWorkingMessage(self, message: str | None = None) -> None:  # noqa: N802
        pass

    def setWorkingVisible(self, visible: bool) -> None:  # noqa: N802
        pass

    def setWorkingIndicator(self, indicator: Any | None = None) -> None:  # noqa: N802
        pass


class _NoOpUIContext(ExtensionUIContext):
    pass


NO_OP_UI_CONTEXT = _NoOpUIContext()


class ExtensionTool(BaseTool):
    """The tool implementation an extension's ``execute`` callable is wrapped in."""

    def __init__(
        self,
        *,
        name: str,
        description: str,
        parameters: dict[str, Any],
        params_model: Any,
        execute_fn: Callable[[dict[str, Any], dict[str, Any] | None], Any],
        owner: str,
        mutating: bool = True,
        label: str | None = None,
        ui_block: type | None = None,
    ) -> None:
        self.name = name
        self.description = description
        self.parameters = parameters
        self.params = params_model
        self._execute = execute_fn
        self.owner = owner
        self.mutating = mutating
        self.label = label or name
        self.ui_block = ui_block
        self.tool_icon = "↪"

    async def execute(self, params: Any, cancel_event: Any | None = None) -> ToolResult:
        args = (
            params.model_dump(exclude_none=True) if hasattr(params, "model_dump") else dict(params)
        )
        ctx = {"cancel_event": cancel_event, "cwd": Path.cwd().as_posix()}
        result = self._execute(args, ctx)
        if inspect.isawaitable(result):
            if cancel_event is not None:
                # Race the call against cancellation, so an interrupted tool
                # reports that rather than vanishing.
                task = asyncio.ensure_future(result)
                cancel_task = asyncio.ensure_future(cancel_event.wait())
                try:
                    done, _ = await asyncio.wait(
                        {task, cancel_task}, return_when=asyncio.FIRST_COMPLETED
                    )
                    if task in done:
                        result = task.result()
                    else:
                        task.cancel()
                        return ToolResult(
                            success=False, result="Extension tool execution was interrupted."
                        )
                finally:
                    if not cancel_task.done():
                        cancel_task.cancel()
            else:
                result = await result

        if result is None:
            return ToolResult(success=True, result="(no output)")
        if isinstance(result, ToolResult):
            return result
        if isinstance(result, dict):
            return ToolResult(**result)
        return ToolResult(success=True, result=str(result))

    def format_call(self, params: Any) -> str:
        data = (
            params.model_dump(exclude_none=True) if hasattr(params, "model_dump") else dict(params)
        )
        return " / ".join(f"{k}={v}" for k, v in data.items())

    def format_preview(self, params: Any) -> str | None:
        return None


def blocking_result(results: list[Any]) -> dict[str, Any]:
    """Merge the handler returns a blocking event collected into one decision."""
    from vtx.plugins.event_contract import merge_results

    return merge_results(results)


__all__ = [
    "BLOCK",
    "NO_OP_UI_CONTEXT",
    "REASON",
    "CommandOutcome",
    "ExtensionAPI",
    "ExtensionActions",
    "ExtensionCommand",
    "ExtensionError",
    "ExtensionFlag",
    "ExtensionNativeProviderRegistration",
    "ExtensionProviderRegistration",
    "ExtensionRecord",
    "ExtensionShortcut",
    "ExtensionTool",
    "ExtensionUIContext",
    "blocking_result",
]

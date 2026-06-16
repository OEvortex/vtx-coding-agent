"""
Extension system for vtx.

Extensions are Python modules (single ``.py`` file or package directory with
``__init__.py``) that expose a top-level ``register(api)`` callable. Through
the :class:`ExtensionAPI` they can:

- subscribe to agent lifecycle events (``api.on(event, handler)``)
- add new LLM-callable tools (``api.register_tool(definition)``)
- add new slash commands (``api.register_command(name, definition)``)
- post UI notifications (``api.notify(message, level)``)

Discovery happens from four places, in this order (later wins on name conflict):

1. project-local ``.vtx/extensions/*.py`` (and ``*/__init__.py``)
2. global ``~/.vtx/agent/extensions/*.py`` (and ``*/__init__.py``)
3. ``extensions:`` list in ``config.yml``
4. ``--extension PATH`` repeated CLI flag (passed in from ``cli.py``)

Set ``--no-extensions`` to skip auto-discovery; only explicit ``--extension``
paths will load.

Extensions run in-process with the same permissions as the vtx process. Like
pi, this is intentional: we want extensions to be able to do everything the
user can do. Don't load extensions from sources you don't trust.

Event handlers can be sync or async. Handlers for blocking events (``tool_call``)
must return a dict to take effect::

    {"block": True, "reason": "rm -rf denied"}
    {"args": {"path": "/safe/alternative"}}
    {"output": "redacted text"}

For non-blocking events, returning a dict is allowed but ignored. Handler
exceptions are logged to stderr and never crash the agent loop.
"""

from __future__ import annotations

import importlib.util
import inspect
import logging
import sys
import traceback
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, get_args

from pydantic import BaseModel, Field, create_model

from . import config as vtx_config
from .config import get_config_dir
from .core.types import ImageContent, TextContent, ToolResult
from .tools.base import BaseTool

log = logging.getLogger("vtx.extensions")

# Public event names an extension can subscribe to. Mirrors the vtx event stream
# plus pi-style blocking points. Keep this set in sync with the docstring above.
SESSION_START = "session_start"
SESSION_END = "session_end"
AGENT_START = "agent_start"
AGENT_END = "agent_end"
TURN_START = "turn_start"
TURN_END = "turn_end"
TOOL_CALL = "tool_call"
TOOL_RESULT = "tool_result"
COMPACTION_START = "compaction_start"
COMPACTION_END = "compaction_end"

ALL_EVENTS: tuple[str, ...] = (
    SESSION_START,
    SESSION_END,
    AGENT_START,
    AGENT_END,
    TURN_START,
    TURN_END,
    TOOL_CALL,
    TOOL_RESULT,
    COMPACTION_START,
    COMPACTION_END,
)

# Handler return-value keys
_BLOCK = "block"
_REASON = "reason"
_ARGS = "args"
_OUTPUT = "output"

# These are blocking events: a handler return value can stop or modify the
# action in progress. Other events are observational.
BLOCKING_EVENTS: frozenset[str] = frozenset({TOOL_CALL, TOOL_RESULT})


# =============================================================================
# Registry
# =============================================================================


@dataclass
class Extension:
    """A loaded extension and everything it contributed to the agent."""

    name: str
    path: Path
    # Tools registered via ``api.register_tool``. Key is the tool name.
    # Replaces the built-in tool with the same name (pi's behavior).
    tools: dict[str, BaseTool] = field(default_factory=dict)
    # Commands registered via ``api.register_command``. Key is the slash name
    # without the leading ``/``.
    commands: dict[str, ExtensionCommand] = field(default_factory=dict)
    # Handler name -> call count, populated as handlers fire (debug only).
    handler_calls: dict[str, int] = field(default_factory=dict)


@dataclass
class ExtensionCommand:
    """A slash command contributed by an extension."""

    name: str
    description: str
    handler: Callable[[str], CommandOutcome]
    owner: str  # Extension that registered it (for /help and conflict warnings)


@dataclass
class CommandOutcome:
    """What an extension command returns to the agent runtime."""

    output: str = ""
    success: bool = True
    exit_after: bool = False  # True for commands that should quit the session


# =============================================================================
# Event bus
# =============================================================================


class EventBus:
    """Async event bus for extension handlers.

    Handler return value semantics:

    - Observational events: return value is ignored.
    - ``tool_call``: dict can contain ``{"block": True, "reason": "..."}`` to
      prevent execution, or ``{"args": {...}}`` to replace the args. First
      handler that returns ``block=True`` short-circuits.
    - ``tool_result``: dict can contain ``{"output": "..."}`` to replace the
      text the LLM sees. Modifications are chained (later handlers see what
      earlier handlers returned).
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[Callable[..., Any]]] = defaultdict(list)

    def on(self, event: str, handler: Callable[..., Any] | None = None) -> Any:
        """Subscribe ``handler`` to a lifecycle event.

        Two call forms are supported::

            bus.on(EVENT, handler)        # explicit handler
            @bus.on(EVENT)                # decorator factory
            def fn(...): ...

        For ``tool_call`` the handler can return ``{"block": True, "reason": "..."}``
        to deny the call, or ``{"args": {...}}`` to rewrite the arguments.
        For ``tool_result`` it can return ``{"output": "..."}`` to replace the
        text the LLM sees.
        """
        if event not in ALL_EVENTS:
            raise ValueError(f"Unknown event {event!r}. Valid events: {', '.join(ALL_EVENTS)}")
        if handler is None:
            # Decorator form
            def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
                self._handlers[event].append(fn)
                return fn

            return decorator
        self._handlers[event].append(handler)
        return handler

    def handler_count(self, event: str) -> int:
        return len(self._handlers.get(event, ()))

    def emit_sync(self, event: str, **payload: Any) -> dict[str, Any]:
        """Synchronous, async-unaware event emit.

        Used for events that must fire before the asyncio loop is fully
        running (``session_start`` on TUI mount, ``session_end`` on shutdown).
        Async handlers are *not* awaited here; they will not be invoked and
        should be deferred from a sync handler by scheduling their work via
        ``asyncio.ensure_future`` from inside the async runtime.
        """
        merged: dict[str, Any] = {}
        for handler in list(self._handlers.get(event, ())):
            try:
                result = handler(event, payload)
            except Exception:
                traceback.print_exc(file=sys.stderr)
                continue

            if inspect.isawaitable(result):
                # Sync emit cannot await; bail so we don't silently drop work.
                log.warning(
                    "extension handler %r for %s returned a coroutine; "
                    "use api.on(...) with a sync function or defer to async emit",
                    _qualname(handler),
                    event,
                )
                continue

            if not isinstance(result, dict):
                continue

            if result.get(_BLOCK):
                merged[_BLOCK] = True
                if _REASON in result:
                    merged[_REASON] = result[_REASON]
                return merged

            if _ARGS in result:
                payload[_ARGS] = result[_ARGS]
                merged[_ARGS] = result[_ARGS]

            if _OUTPUT in result:
                payload[_OUTPUT] = result[_OUTPUT]
                merged[_OUTPUT] = result[_OUTPUT]

            merged.update({k: v for k, v in result.items() if k not in (_BLOCK, _ARGS, _OUTPUT)})

        return merged

    async def emit(
        self, event: str, *, cancel_event: Any | None = None, **payload: Any
    ) -> dict[str, Any]:
        """Fire ``event`` with ``payload``.

        Returns a merged dict of handler modifications. For ``tool_call`` and
        ``tool_result``, callers should treat the returned ``block`` flag as
        authoritative.
        """
        merged: dict[str, Any] = {}
        for handler in list(self._handlers.get(event, ())):
            try:
                result = handler(event, payload)
                if inspect.isawaitable(result):
                    if cancel_event is not None:
                        result = await _await_or_cancel(result, cancel_event)
                    else:
                        result = await result
            except Exception:
                # Never let a buggy extension crash the agent.
                traceback.print_exc(file=sys.stderr)
                continue

            if not isinstance(result, dict):
                continue

            if result.get(_BLOCK):
                # Short-circuit: first blocker wins. We keep their reason.
                merged[_BLOCK] = True
                if _REASON in result:
                    merged[_REASON] = result[_REASON]
                return merged

            if _ARGS in result:
                payload[_ARGS] = result[_ARGS]
                merged[_ARGS] = result[_ARGS]

            if _OUTPUT in result:
                payload[_OUTPUT] = result[_OUTPUT]
                merged[_OUTPUT] = result[_OUTPUT]

            merged.update({k: v for k, v in result.items() if k not in (_BLOCK, _ARGS, _OUTPUT)})

        return merged


async def _await_or_cancel(awaitable: Any, cancel_event: Any) -> Any:
    """Await ``awaitable`` or return ``None`` if ``cancel_event`` fires first.

    Mirrors the behavior used by the turn runner so extension handlers that
    block on a long operation can be aborted on user interrupt.
    """
    import asyncio

    task = asyncio.ensure_future(awaitable)
    cancel_task = asyncio.ensure_future(cancel_event.wait()) if cancel_event else None
    try:
        if cancel_task is None:
            return await task
        done, _ = await asyncio.wait({task, cancel_task}, return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            return task.result()
        task.cancel()
        return None
    finally:
        if cancel_task and not cancel_task.done():
            cancel_task.cancel()


async def _await_coroutine(awaitable: Any) -> Any:
    return await awaitable


# =============================================================================
# Extension API
# =============================================================================


class ExtensionAPI:
    """The object passed to an extension's ``register(api)`` function.

    Extensions call methods on this object to subscribe to events, register
    tools, and register commands. They should not retain ``api`` beyond the
    duration of ``register()``; the runtime owns the canonical state.
    """

    def __init__(
        self,
        extension: Extension,
        bus: EventBus,
        *,
        cwd: str,
        session_file: str | None,
        config_dir: Path,
    ) -> None:
        self._extension = extension
        self._bus = bus
        self.cwd = cwd
        self.session_file = session_file
        self.config_dir = config_dir

    # ---- events ----------------------------------------------------------

    def on(self, event: str, handler: Callable[..., Any] | None = None) -> Any:
        """Subscribe ``handler`` to a lifecycle event.

        Two call forms are supported::

            api.on(EVENT, handler)        # explicit handler
            @api.on(EVENT)                # decorator factory
            def fn(...): ...

        For ``tool_call`` the handler can return ``{"block": True, "reason": "..."}``
        to deny the call, or ``{"args": {...}}`` to rewrite the arguments.
        For ``tool_result`` it can return ``{"output": "..."}`` to replace
        the text the LLM sees.
        """
        if handler is None:

            def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
                self._bus.on(event, fn)
                self._extension.handler_calls[_qualname(fn)] = 0
                return fn

            return decorator
        self._bus.on(event, handler)
        self._extension.handler_calls[_qualname(handler)] = 0
        return handler

    # ---- tool registration ----------------------------------------------

    def register_tool(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        *,
        execute: Callable[[dict[str, Any], dict[str, Any] | None], Any],
        mutating: bool = True,
        label: str | None = None,
    ) -> BaseTool:
        """Register a new LLM-callable tool, or override a built-in.

        ``parameters`` is a JSON Schema object (the same shape providers
        receive). The pydantic model used by the agent loop is generated from
        it, so extensions can use any JSON-Schema-compliant type.

        ``execute`` is called with ``(args_dict, ctx_dict)`` and may be sync
        or async. It must return a :class:`ToolResult`-like object (a dict
        with the same keys also works).
        """
        if not name or not isinstance(name, str):
            raise ValueError("Tool name must be a non-empty string")
        params_model = _json_schema_to_pydantic(name, parameters)
        tool = ExtensionTool(
            name=name,
            description=description,
            parameters=parameters,
            params_model=params_model,
            execute_fn=execute,
            owner=self._extension.name,
            mutating=mutating,
            label=label or name,
        )
        self._extension.tools[name] = tool
        return tool

    # ---- command registration -------------------------------------------

    def register_command(
        self, name: str, description: str, handler: Callable[[str], CommandOutcome | str | None]
    ) -> ExtensionCommand:
        """Register a new ``/slash`` command.

        ``handler`` is called with the argument string (everything after
        ``/name``). Return a :class:`CommandOutcome`, a string (treated as
        ``output``), or ``None`` (silently succeeded).
        """
        if not name or not isinstance(name, str):
            raise ValueError("Command name must be a non-empty string")
        if name.startswith("/"):
            name = name.lstrip("/")

        def _wrapper(args: str) -> CommandOutcome:
            result = handler(args)
            if result is None:
                return CommandOutcome(output="")
            if isinstance(result, CommandOutcome):
                return result
            return CommandOutcome(output=str(result))

        cmd = ExtensionCommand(
            name=name, description=description, handler=_wrapper, owner=self._extension.name
        )
        self._extension.commands[name] = cmd
        return cmd

    # ---- notifications ---------------------------------------------------

    def notify(self, message: str, level: Literal["info", "warning", "error"] = "info") -> None:
        """Emit a user-facing notification.

        In TUI mode this prints to stderr (the chat log surfaces stderr
        lines). In headless mode it goes to stderr only.
        """
        prefix = {
            "info": "[extension]",
            "warning": "[extension:warn]",
            "error": "[extension:error]",
        }.get(level, "[extension]")
        print(f"{prefix} {self._extension.name}: {message}", file=sys.stderr)


# =============================================================================
# ExtensionTool
# =============================================================================


class ExtensionTool(BaseTool):
    """Adapter that wraps an extension's user-supplied ``execute`` callback.

    The user-facing tool definition accepts a JSON Schema for ``parameters``;
    we synthesize a pydantic model at registration time so the agent loop can
    keep using its existing ``tool.params(**arguments)`` validation path.
    """

    def __init__(
        self,
        *,
        name: str,
        description: str,
        parameters: dict[str, Any],
        params_model: type[BaseModel],
        execute_fn: Callable[..., Any],
        owner: str,
        mutating: bool,
        label: str,
    ) -> None:
        self.name = name
        self.description = description
        self._parameters = parameters
        self.params = params_model
        self._execute_fn = execute_fn
        self._owner = owner
        self.mutating = mutating
        self.tool_icon = "↪"  # mark extension tools in the UI
        self.label = label

    async def execute(self, params: BaseModel, cancel_event: Any | None = None) -> ToolResult:
        import asyncio

        args_dict = params.model_dump(exclude_none=True)
        ctx = {"cancel_event": cancel_event, "cwd": Path.cwd().as_posix()}

        result = self._execute_fn(args_dict, ctx)
        if inspect.isawaitable(result):
            if cancel_event is not None:
                # Race the call against cancellation
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

    def format_call(self, params: BaseModel) -> str:
        # Use the parent class's behaviour (key=value pairs) for consistency.
        return super().format_call(params)


# =============================================================================
# JSON Schema -> pydantic
# =============================================================================


def _json_schema_to_pydantic(tool_name: str, schema: dict[str, Any]) -> type[BaseModel]:
    """Convert a JSON Schema for tool parameters into a pydantic ``BaseModel``.

    We support the subset of JSON Schema that providers accept: ``type: object``
    with ``properties`` and ``required``. Property types map to native Python /
    pydantic types. Anything we don't understand is left as ``Any``, which
    means a slightly looser contract but never blocks an extension.

    Constraints like ``enum``, ``minLength``, ``pattern`` are preserved in
    the generated model's JSON schema via ``Field(json_schema_extra=...)``
    so they round-trip back to the LLM provider.
    """
    if schema.get("type") not in (None, "object"):
        raise ValueError(
            f"Extension tool {tool_name!r}: parameters.type must be "
            f"'object' (got {schema.get('type')!r})"
        )
    properties: dict[str, Any] = schema.get("properties") or {}
    required: set[str] = set(schema.get("required") or [])

    fields: dict[str, Any] = {}
    for prop_name, prop_schema in properties.items():
        py_type = _json_type_to_python(prop_schema)
        description = prop_schema.get("description") if isinstance(prop_schema, dict) else None
        extra: dict[str, Any] = {}
        if isinstance(prop_schema, dict):
            for key in ("enum", "minLength", "maxLength", "minimum", "maximum", "pattern"):
                if key in prop_schema:
                    extra[key] = prop_schema[key]
        field_kwargs: dict[str, Any] = {"description": description}
        if extra:
            field_kwargs["json_schema_extra"] = extra
        if prop_name in required:
            fields[prop_name] = (py_type, Field(..., **field_kwargs))
        else:
            fields[prop_name] = (py_type, Field(default=None, **field_kwargs))

    if not fields:
        # An empty schema would be ambiguous; default to a single optional
        # ``input`` field so the LLM always has something concrete to send.
        fields["input"] = (str | None, Field(default=None, description="Optional input"))

    model_name = f"{_safe_class_name(tool_name)}_Params"
    return create_model(model_name, **fields)  # type: ignore[call-overload]


def _json_type_to_python(prop_schema: Any) -> Any:
    """Map a single property's JSON Schema to a Python type annotation."""
    if not isinstance(prop_schema, dict):
        return Any

    json_type = prop_schema.get("type")

    if isinstance(json_type, list):
        # Nullable unions ("type": ["string", "null"]) -- pick the first non-null.
        for t in json_type:
            if t != "null":
                json_type = t
                break

    if json_type == "string":
        # We deliberately do not translate ``enum`` into a Python ``Literal``
        # here: the type-checker cannot validate runtime enum values, and
        # pydantic does not enforce them from JSON schema automatically.
        # The enum constraint lives in the JSON schema we hand to the LLM
        # provider, so most providers will reject invalid values upstream.
        return str
    if json_type == "integer":
        return int
    if json_type == "number":
        return float
    if json_type == "boolean":
        return bool
    if json_type == "array":
        inner = _json_type_to_python(prop_schema.get("items") or {})
        return list[inner]  # type: ignore[valid-type]
    if json_type == "object":
        return dict[str, Any]
    if json_type == "null":
        return None
    return Any


def _safe_class_name(tool_name: str) -> str:
    cleaned = "".join(c if c.isalnum() else "_" for c in tool_name.title())
    if cleaned and cleaned[0].isdigit():
        cleaned = "T_" + cleaned
    return cleaned or "Extension"


# =============================================================================
# Discovery and loading
# =============================================================================


def _candidate_paths(extension_dir: Path) -> list[Path]:
    """Return sorted .py files and package ``__init__.py`` entries in ``extension_dir``."""
    if not extension_dir.is_dir():
        return []
    found: list[Path] = []
    for entry in sorted(extension_dir.iterdir()):
        if entry.is_file() and entry.suffix == ".py" and entry.name != "__init__.py":
            found.append(entry)
        elif entry.is_dir() and (entry / "__init__.py").is_file():
            found.append(entry / "__init__.py")
    return found


def discover_extension_paths(
    *, cwd: str, configured: Iterable[str] | None = None, agent_dir: Path | None = None
) -> list[Path]:
    """Resolve the list of extension paths to load, in priority order.

    Project-local extensions come first so they can override global ones
    (matching pi's behavior).
    """
    configured_paths: list[Path] = [Path(p).expanduser() for p in (configured or [])]
    resolved_agent_dir = agent_dir or (get_config_dir() / "agent")

    seen: set[Path] = set()
    ordered: list[Path] = []

    def _add(p: Path) -> None:
        try:
            resolved = p.resolve()
        except OSError:
            return
        if resolved in seen or not resolved.exists():
            return
        seen.add(resolved)
        ordered.append(resolved)

    project_dir = Path(cwd) / ".vtx" / "extensions"
    for candidate in _candidate_paths(project_dir):
        _add(candidate)

    global_dir = resolved_agent_dir / "extensions"
    for candidate in _candidate_paths(global_dir):
        _add(candidate)

    for configured_path in configured_paths:
        if configured_path.is_dir():
            # If the configured path is itself a package (directory with
            # __init__.py), load its __init__.py as the entry point.
            if (configured_path / "__init__.py").is_file():
                _add(configured_path / "__init__.py")
                continue
            for candidate in _candidate_paths(configured_path):
                _add(candidate)
        elif configured_path.exists():
            _add(configured_path)

    return ordered


def load_extension(
    path: Path, *, bus: EventBus, cwd: str, session_file: str | None, config_dir: Path
) -> Extension:
    """Import a single extension file or package and run its ``register`` hook."""
    module_name = f"vtx_ext_{abs(hash(path.as_posix()))}"
    spec = importlib.util.spec_from_file_location(module_name, str(path))
    if spec is None or spec.loader is None:
        raise ExtensionLoadError(f"Could not import extension at {path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise ExtensionLoadError(f"Extension {path} failed to import: {exc}") from exc

    register = getattr(module, "register", None)
    if register is None:
        raise ExtensionLoadError(
            f"Extension {path} does not export a top-level `register(api)` function"
        )
    if not callable(register):
        raise ExtensionLoadError(
            f"Extension {path}: `register` must be callable, got {type(register).__name__}"
        )

    name = getattr(module, "__ext_name__", None) or path.stem
    extension = Extension(name=name, path=path)
    api = ExtensionAPI(extension, bus, cwd=cwd, session_file=session_file, config_dir=config_dir)
    try:
        result = register(api)
    except Exception as exc:
        raise ExtensionLoadError(f"Extension {path}: register(api) raised: {exc}") from exc
    if inspect.isawaitable(result):
        # Async factories are out of scope for v0.1.3 but we surface a clear
        # error so users don't silently lose work.
        raise ExtensionLoadError(
            f"Extension {path}: async register() is not supported in v0.1.3; use a sync function"
        )
    return extension


def load_all_extensions(
    *,
    cwd: str,
    configured: Iterable[str] | None = None,
    bus: EventBus | None = None,
    session_file: str | None = None,
    agent_dir: Path | None = None,
    config_dir: Path | None = None,
) -> tuple[list[Extension], list[str], EventBus]:
    """Discover and load every extension. Returns ``(extensions, errors, bus)``.

    Errors are collected, not raised: one bad extension should not block
    loading the rest. The caller decides how to surface them. The bus is
    returned so callers that want the live bus (e.g. ``load_for_runtime``)
    don't need to pass one in.
    """
    bus = bus or EventBus()
    paths = discover_extension_paths(cwd=cwd, configured=configured, agent_dir=agent_dir)
    extensions: list[Extension] = []
    errors: list[str] = []
    for path in paths:
        try:
            extensions.append(
                load_extension(
                    path,
                    bus=bus,
                    cwd=cwd,
                    session_file=session_file,
                    config_dir=config_dir or get_config_dir(),
                )
            )
        except ExtensionLoadError as exc:
            errors.append(str(exc))
    return extensions, errors, bus


class ExtensionLoadError(RuntimeError):
    """Raised when an extension cannot be loaded (bad file, missing register, etc.)."""


def _qualname(obj: Any) -> str:
    """Return ``obj.__qualname__`` if present, else ``repr(obj)``.

    Centralizes the safe-attribute access so call sites stay short.
    """
    return getattr(obj, "__qualname__", None) or repr(obj)


# =============================================================================
# Integration helpers for the rest of vtx
# =============================================================================


@dataclass
class LoadedExtensions:
    """Snapshot of loaded extensions, passed to the agent and the UI."""

    extensions: list[Extension] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    bus: EventBus = field(default_factory=EventBus)
    config_dir: Path = field(default_factory=get_config_dir)

    @property
    def all_commands(self) -> dict[str, ExtensionCommand]:
        merged: dict[str, ExtensionCommand] = {}
        for ext in self.extensions:
            for name, cmd in ext.commands.items():
                if name in merged:
                    log.warning(
                        "extension command /%s overridden by %s (was %s)",
                        name,
                        ext.name,
                        merged[name].owner,
                    )
                merged[name] = cmd
        return merged

    def list_extension_tools(self) -> list[BaseTool]:
        tools: list[BaseTool] = []
        for ext in self.extensions:
            for name, tool in ext.tools.items():
                if name in {t.name for t in tools}:
                    log.warning(
                        "extension tool %r from %s shadows an earlier registration", name, ext.name
                    )
                tools.append(tool)
        return tools

    def describe(self) -> list[dict[str, Any]]:
        """For ``/extensions`` (TUI) and the headless ``--list-extensions`` flag."""
        rows: list[dict[str, Any]] = []
        for ext in self.extensions:
            rows.append(
                {
                    "name": ext.name,
                    "path": str(ext.path),
                    "tools": sorted(ext.tools.keys()),
                    "commands": sorted(ext.commands.keys()),
                    "handlers": sorted({_qualname(h) for h in ext.handler_calls}),
                }
            )
        return rows


def load_for_runtime(
    cwd: str,
    *,
    extra_paths: Iterable[str] | None = None,
    auto_discover: bool = True,
    session_file: str | None = None,
) -> LoadedExtensions:
    """Convenience entry point used by ``runtime.py`` and the TUI launch path.

    ``extra_paths`` are added on top of ``config.extensions`` and the
    auto-discovered directories. ``auto_discover=False`` skips the
    ``.vtx/extensions`` and ``~/.vtx/agent/extensions`` directories; the
    user-supplied paths still load.
    """
    configured: list[str] = []
    if auto_discover:
        configured.extend(vtx_config.extensions)
    if extra_paths:
        configured.extend(extra_paths)

    if auto_discover:
        exts, errors, bus = load_all_extensions(
            cwd=cwd, configured=configured, session_file=session_file
        )
    else:
        # Only honor explicit paths when discovery is off
        bus = EventBus()
        exts, errors = [], []
        for path_str in configured:
            path = Path(path_str).expanduser()
            if path.is_dir():
                for candidate in _candidate_paths(path):
                    try:
                        exts.append(
                            load_extension(
                                candidate,
                                bus=bus,
                                cwd=cwd,
                                session_file=session_file,
                                config_dir=get_config_dir(),
                            )
                        )
                    except ExtensionLoadError as exc:
                        errors.append(str(exc))
            elif path.exists():
                try:
                    exts.append(
                        load_extension(
                            path,
                            bus=bus,
                            cwd=cwd,
                            session_file=session_file,
                            config_dir=get_config_dir(),
                        )
                    )
                except ExtensionLoadError as exc:
                    errors.append(str(exc))

    return LoadedExtensions(extensions=exts, errors=errors, bus=bus)


# Suppress "imported but unused" for the typing-only imports above.
_ = (get_args, ImageContent, TextContent, Field)

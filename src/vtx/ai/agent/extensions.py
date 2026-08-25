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

import asyncio
import contextlib
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

from vtx.ai.agent.tools.base import BaseTool
from vtx.core.paths import get_config_dir
from vtx.core.types import ImageContent, TextContent, ToolResult

log = logging.getLogger("agent.extensions")

# Public event names an extension can subscribe to. Mirrors the vtx event stream
# plus pi-style blocking points. Keep this set in sync with the docstring above.
SESSION_START = "session_start"
SESSION_END = "session_end"
SESSION_INFO_CHANGED = "session_info_changed"
SESSION_BEFORE_SWITCH = "session_before_switch"
SESSION_BEFORE_FORK = "session_before_fork"
SESSION_BEFORE_COMPACT = "session_before_compact"
SESSION_COMPACT = "session_compact"
SESSION_COMPACT_FAILED = "session_compact_failed"
SESSION_SHUTDOWN = "session_shutdown"
SESSION_BEFORE_TREE = "session_before_tree"
SESSION_TREE = "session_tree"
AGENT_START = "agent_start"
AGENT_END = "agent_end"
AGENT_SETTLED = "agent_settled"
TURN_START = "turn_start"
TURN_END = "turn_end"
MESSAGE_START = "message_start"
MESSAGE_UPDATE = "message_update"
MESSAGE_END = "message_end"
TOOL_CALL = "tool_call"
TOOL_RESULT = "tool_result"
TOOL_EXECUTION_START = "tool_execution_start"
TOOL_EXECUTION_UPDATE = "tool_execution_update"
TOOL_EXECUTION_END = "tool_execution_end"
COMPACTION_START = "compaction_start"
COMPACTION_END = "compaction_end"
# Agent lifecycle events (re-exported from vtx.agents for convenience).
# The constants live in vtx.agents; importing them here avoids a cycle
# at the call sites that import from vtx.extensions.
AGENT_ACTIVATED = "agent_activated"
AGENT_CHANGED = "agent_changed"
TOOL_GROUP_CHANGED = "tool_group_changed"
# Pi-parity events
PROJECT_TRUST = "project_trust"
RESOURCES_DISCOVER = "resources_discover"
CONTEXT = "context"
BEFORE_PROVIDER_REQUEST = "before_provider_request"
BEFORE_PROVIDER_HEADERS = "before_provider_headers"
AFTER_PROVIDER_RESPONSE = "after_provider_response"
BEFORE_AGENT_START = "before_agent_start"
MODEL_SELECT = "model_select"
THINKING_LEVEL_SELECT = "thinking_level_select"
USER_BASH = "user_bash"
INPUT = "input"

ALL_EVENTS: tuple[str, ...] = (
    SESSION_START,
    SESSION_END,
    SESSION_INFO_CHANGED,
    SESSION_BEFORE_SWITCH,
    SESSION_BEFORE_FORK,
    SESSION_BEFORE_COMPACT,
    SESSION_COMPACT,
    SESSION_COMPACT_FAILED,
    SESSION_SHUTDOWN,
    SESSION_BEFORE_TREE,
    SESSION_TREE,
    AGENT_START,
    AGENT_END,
    AGENT_SETTLED,
    TURN_START,
    TURN_END,
    MESSAGE_START,
    MESSAGE_UPDATE,
    MESSAGE_END,
    TOOL_CALL,
    TOOL_RESULT,
    TOOL_EXECUTION_START,
    TOOL_EXECUTION_UPDATE,
    TOOL_EXECUTION_END,
    COMPACTION_START,
    COMPACTION_END,
    AGENT_ACTIVATED,
    AGENT_CHANGED,
    TOOL_GROUP_CHANGED,
    PROJECT_TRUST,
    RESOURCES_DISCOVER,
    CONTEXT,
    BEFORE_PROVIDER_REQUEST,
    BEFORE_PROVIDER_HEADERS,
    AFTER_PROVIDER_RESPONSE,
    BEFORE_AGENT_START,
    MODEL_SELECT,
    THINKING_LEVEL_SELECT,
    USER_BASH,
    INPUT,
)

# Handler return-value keys
_BLOCK = "block"
_REASON = "reason"
_ARGS = "args"
_OUTPUT = "output"

# These are blocking events: a handler return value can stop or modify the
# action in progress. Other events are observational.
BLOCKING_EVENTS: frozenset[str] = frozenset(
    {
        TOOL_CALL,
        TOOL_RESULT,
        SESSION_BEFORE_SWITCH,
        SESSION_BEFORE_FORK,
        SESSION_BEFORE_COMPACT,
        SESSION_BEFORE_TREE,
    }
)

# =============================================================================
# Source info
# =============================================================================


@dataclass
class SourceInfo:
    """Provenance metadata for an extension registration."""

    path: str
    source: str
    scope: str = "temporary"
    origin: str = "top-level"
    base_dir: str | None = None


# =============================================================================
# Event payload types
# =============================================================================


@dataclass
class SessionInfoChangedEvent:
    type: Literal["session_info_changed"] = "session_info_changed"
    name: str | None = None


@dataclass
class SessionBeforeSwitchEvent:
    type: Literal["session_before_switch"] = "session_before_switch"
    reason: str = "new"
    target_session_file: str | None = None


@dataclass
class SessionBeforeForkEvent:
    type: Literal["session_before_fork"] = "session_before_fork"
    entry_id: str = ""
    position: str = "at"


@dataclass
class SessionBeforeCompactEvent:
    type: Literal["session_before_compact"] = "session_before_compact"
    tokens_before: int = 0
    reason: str = "manual"
    will_retry: bool = False
    aborted: bool = False


@dataclass
class SessionCompactEvent:
    type: Literal["session_compact"] = "session_compact"
    tokens_before: int = 0
    tokens_after: int = 0
    reason: str = "manual"
    will_retry: bool = False
    from_extension: bool = False


@dataclass
class SessionCompactFailedEvent:
    type: Literal["session_compact_failed"] = "session_compact_failed"
    reason: str = "manual"
    error_message: str | None = None
    aborted: bool = False
    will_retry: bool = False
    from_extension: bool = False


@dataclass
class SessionShutdownEvent:
    type: Literal["session_shutdown"] = "session_shutdown"
    reason: str = "quit"
    target_session_file: str | None = None


@dataclass
class SessionBeforeTreeEvent:
    type: Literal["session_before_tree"] = "session_before_tree"
    target_id: str = ""
    signal: Any = None


@dataclass
class SessionTreeEvent:
    type: Literal["session_tree"] = "session_tree"
    new_leaf_id: str | None = None
    old_leaf_id: str | None = None


@dataclass
class AgentSettledEvent:
    type: Literal["agent_settled"] = "agent_settled"


@dataclass
class MessageStartEvent:
    type: Literal["message_start"] = "message_start"
    message: Any = None


@dataclass
class MessageUpdateEvent:
    type: Literal["message_update"] = "message_update"
    message: Any = None


@dataclass
class MessageEndEvent:
    type: Literal["message_end"] = "message_end"
    message: Any = None


@dataclass
class ToolExecutionStartEvent:
    type: Literal["tool_execution_start"] = "tool_execution_start"
    tool_call_id: str = ""
    tool_name: str = ""
    args: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolExecutionUpdateEvent:
    type: Literal["tool_execution_update"] = "tool_execution_update"
    tool_call_id: str = ""
    tool_name: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    partial_result: Any = None


@dataclass
class ToolExecutionEndEvent:
    type: Literal["tool_execution_end"] = "tool_execution_end"
    tool_call_id: str = ""
    tool_name: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    result: Any = None
    is_error: bool = False


@dataclass
class ProjectTrustEvent:
    type: Literal["project_trust"] = "project_trust"
    cwd: str = ""


@dataclass
class ResourcesDiscoverEvent:
    type: Literal["resources_discover"] = "resources_discover"
    cwd: str = ""
    reason: str = "startup"


@dataclass
class ContextEvent:
    type: Literal["context"] = "context"
    messages: list[Any] = field(default_factory=list)


@dataclass
class BeforeProviderRequestEvent:
    type: Literal["before_provider_request"] = "before_provider_request"
    payload: Any = None


@dataclass
class BeforeProviderHeadersEvent:
    type: Literal["before_provider_headers"] = "before_provider_headers"
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class AfterProviderResponseEvent:
    type: Literal["after_provider_response"] = "after_provider_response"
    status: int = 0
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class BeforeAgentStartEvent:
    type: Literal["before_agent_start"] = "before_agent_start"
    prompt: str = ""
    images: list[Any] = field(default_factory=list)
    system_prompt: str = ""
    system_prompt_options: dict[str, Any] = field(default_factory=dict)


@dataclass
class ModelSelectEvent:
    type: Literal["model_select"] = "model_select"
    model: str = ""
    previous_model: str | None = None
    source: str = "set"


@dataclass
class ThinkingLevelSelectEvent:
    type: Literal["thinking_level_select"] = "thinking_level_select"
    level: str = "high"
    previous_level: str = "high"


@dataclass
class UserBashEvent:
    type: Literal["user_bash"] = "user_bash"
    command: str = ""
    exclude_from_context: bool = False
    cwd: str = ""


@dataclass
class InputEvent:
    type: Literal["input"] = "input"
    text: str = ""
    images: list[Any] = field(default_factory=list)
    source: str = "interactive"
    streaming_behavior: str | None = None


# =============================================================================
# Result types
# =============================================================================


@dataclass
class ToolCallEventResult:
    block: bool = False
    reason: str | None = None
    terminate: bool = False
    args: dict[str, Any] | None = None


@dataclass
class ToolResultEventResult:
    content: list[Any] | None = None
    details: Any = None
    is_error: bool | None = None
    output: str | None = None


@dataclass
class UserBashEventResult:
    operations: dict[str, Any] | None = None
    result: dict[str, Any] | None = None


@dataclass
class InputEventResult:
    action: str = "continue"
    text: str | None = None
    images: list[Any] | None = None


@dataclass
class ContextEventResult:
    messages: list[Any] | None = None


@dataclass
class SessionBeforeSwitchResult:
    cancel: bool = False


@dataclass
class SessionBeforeForkResult:
    cancel: bool = False
    skip_conversation_restore: bool = False


@dataclass
class SessionBeforeCompactResult:
    cancel: bool = False
    compaction: dict[str, Any] | None = None


@dataclass
class SessionBeforeTreeResult:
    cancel: bool = False
    summary: str | None = None
    custom_instructions: str | None = None
    replace_instructions: bool = False
    label: str | None = None


@dataclass
class MessageEndEventResult:
    message: Any = None


@dataclass
class BeforeAgentStartEventResult:
    message: dict[str, Any] | None = None
    system_prompt: str | None = None


@dataclass
class ProjectTrustEventResult:
    trusted: str = "undecided"
    remember: bool = False


@dataclass
class ResourcesDiscoverResult:
    skill_paths: list[str] = field(default_factory=list)
    prompt_paths: list[str] = field(default_factory=list)
    theme_paths: list[str] = field(default_factory=list)


# =============================================================================
# Event object helpers
# =============================================================================

_EVENT_CLASS_MAP: dict[str, type[Any]] | None = None


def _get_event_class_map() -> dict[str, type[Any]]:
    global _EVENT_CLASS_MAP
    if _EVENT_CLASS_MAP is None:
        # Agent/Turn lifecycle event objects live in vtx.core.events;
        # all other event classes are defined in this module.
        from vtx.core.events import AgentEndEvent, AgentStartEvent, TurnEndEvent, TurnStartEvent

        _EVENT_CLASS_MAP = {
            SESSION_START: SessionStartEvent,
            SESSION_END: SessionEndEvent,
            SESSION_INFO_CHANGED: SessionInfoChangedEvent,
            SESSION_BEFORE_SWITCH: SessionBeforeSwitchEvent,
            SESSION_BEFORE_FORK: SessionBeforeForkEvent,
            SESSION_BEFORE_COMPACT: SessionBeforeCompactEvent,
            SESSION_COMPACT: SessionCompactEvent,
            SESSION_COMPACT_FAILED: SessionCompactFailedEvent,
            SESSION_SHUTDOWN: SessionShutdownEvent,
            SESSION_BEFORE_TREE: SessionBeforeTreeEvent,
            SESSION_TREE: SessionTreeEvent,
            AGENT_START: AgentStartEvent,
            AGENT_END: AgentEndEvent,
            AGENT_SETTLED: AgentSettledEvent,
            TURN_START: TurnStartEvent,
            TURN_END: TurnEndEvent,
            MESSAGE_START: MessageStartEvent,
            MESSAGE_UPDATE: MessageUpdateEvent,
            MESSAGE_END: MessageEndEvent,
            TOOL_CALL: ToolCallEvent,
            TOOL_RESULT: ToolResultEvent,
            TOOL_EXECUTION_START: ToolExecutionStartEvent,
            TOOL_EXECUTION_UPDATE: ToolExecutionUpdateEvent,
            TOOL_EXECUTION_END: ToolExecutionEndEvent,
            COMPACTION_START: CompactionStartEvent,
            COMPACTION_END: CompactionEndEvent,
            AGENT_ACTIVATED: AgentActivatedEvent,
            AGENT_CHANGED: AgentChangedEvent,
            TOOL_GROUP_CHANGED: ToolGroupChangedEvent,
            PROJECT_TRUST: ProjectTrustEvent,
            RESOURCES_DISCOVER: ResourcesDiscoverEvent,
            CONTEXT: ContextEvent,
            BEFORE_PROVIDER_REQUEST: BeforeProviderRequestEvent,
            BEFORE_PROVIDER_HEADERS: BeforeProviderHeadersEvent,
            AFTER_PROVIDER_RESPONSE: AfterProviderResponseEvent,
            BEFORE_AGENT_START: BeforeAgentStartEvent,
            MODEL_SELECT: ModelSelectEvent,
            THINKING_LEVEL_SELECT: ThinkingLevelSelectEvent,
            USER_BASH: UserBashEvent,
            INPUT: InputEvent,
        }
    return _EVENT_CLASS_MAP


def _build_event_object(event: str, payload: dict[str, Any]) -> Any:
    cls = _get_event_class_map().get(event)
    if cls is None:
        return payload
    try:
        return cls(**payload)
    except Exception:
        return payload


# =============================================================================
# EventBus
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
    # Per-agent local tools registered via ``api.register_local_tool(agent=...)``.
    # Outer key is the agent name; inner key is the tool name. These are only
    # surfaced in the active tool set when their named agent is the active one.
    local_tools: dict[str, dict[str, BaseTool]] = field(default_factory=dict)
    # Handler name -> call count, populated as handlers fire (debug only).
    handler_calls: dict[str, int] = field(default_factory=dict)
    # Shortcuts registered via ``api.register_shortcut``.
    shortcuts: dict[str, Any] = field(default_factory=dict)
    # Flags registered via ``api.register_flag``.
    flags: dict[str, Any] = field(default_factory=dict)
    # Custom message renderers by customType.
    message_renderers: dict[str, Callable[..., Any]] = field(default_factory=dict)
    # Custom entry renderers by customType.
    entry_renderers: dict[str, Callable[..., Any]] = field(default_factory=dict)
    # Event handlers registered via ``api.on``. Key is the event type name.
    handlers: dict[str, list[Callable[..., Any]]] = field(default_factory=dict)


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


@dataclass
class ExtensionShortcut:
    shortcut: str
    description: str | None = None
    handler: Callable[..., Any] | None = None
    extension_path: str = ""


@dataclass
class ExtensionFlag:
    name: str
    description: str | None = None
    type: str = "boolean"
    default: Any = None
    extension_path: str = ""


@dataclass
class ExtensionError:
    extension_path: str
    event: str
    error: str
    stack: str | None = None


@dataclass
class ExtensionProviderRegistration:
    name: str
    config: dict[str, Any]


@dataclass
class ExtensionNativeProviderRegistration:
    provider: Any


# =============================================================================
# Event payload types
# =============================================================================


@dataclass
class SessionStartEvent:
    type: Literal["session_start"] = "session_start"
    cwd: str = ""
    session_id: str = ""


@dataclass
class SessionEndEvent:
    type: Literal["session_end"] = "session_end"
    cwd: str = ""
    session_id: str = ""


@dataclass
class CompactionStartEvent:
    type: Literal["compaction_start"] = "compaction_start"
    tokens_before: int = 0


@dataclass
class CompactionEndEvent:
    type: Literal["compaction_end"] = "compaction_end"
    tokens_before: int = 0
    tokens_after: int = 0
    aborted: bool = False
    reason: str = ""


@dataclass
class AgentActivatedEvent:
    type: Literal["agent_activated"] = "agent_activated"
    agent: str = ""


@dataclass
class AgentChangedEvent:
    type: Literal["agent_changed"] = "agent_changed"
    previous: str | None = None
    current: str | None = None


@dataclass
class ToolGroupChangedEvent:
    type: Literal["tool_group_changed"] = "tool_group_changed"
    agent: str | None = None
    group: str | None = None


@dataclass
class ToolCallEvent:
    type: Literal["tool_call"] = "tool_call"
    tool_call_id: str = ""
    tool_name: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    tool: Any = None


@dataclass
class ToolResultEvent:
    type: Literal["tool_result"] = "tool_result"
    tool_call_id: str = ""
    tool_name: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    result: Any = None
    content: list[Any] | None = None
    details: Any = None
    is_error: bool = False
    output: str | None = None


# =============================================================================
# Event bus
# =============================================================================


@dataclass
class HandlerContext:
    """Per-event context passed to extension handlers that accept a third argument.

    Mirrors pi's ``(event, ctx)`` shape adapted to vtx's ``(event, payload,
    ctx)`` convention: two-argument handlers keep working unchanged, while
    handlers declared as ``(event, payload, ctx)`` receive a
    :class:`HandlerContext` whose ``ui`` exposes the interactive primitives
    (``confirm``, ``select``, ``input``, ``notify``, ...).
    """

    ui: Any = None  # ExtensionUIContext; Any avoids a forward-ref cycle
    cwd: str = ""
    mode: str = "print"


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
        self._ui_context: Any = None
        self._cwd: str = ""
        self._mode: str = "print"
        self._arity_cache: dict[int, bool] = {}

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

    def set_ui_context(self, ui_context: Any, *, cwd: str = "", mode: str = "print") -> None:
        """Install the host UI so handlers get interactive ``ctx.ui`` primitives.

        Called by the TUI at startup with a Textual-backed implementation.
        Without it, handlers receive the safe no-op context.
        """
        self._ui_context = ui_context
        self._cwd = cwd
        self._mode = mode

    def _make_context(self) -> HandlerContext:
        return HandlerContext(
            ui=self._ui_context if self._ui_context is not None else _NO_OP_UI_CONTEXT,
            cwd=self._cwd,
            mode=self._mode,
        )

    @staticmethod
    def _accepts_context(handler: Callable[..., Any]) -> bool:
        """True if ``handler`` can take ``(event, payload, ctx)`` positionally."""
        try:
            sig = inspect.signature(handler)
        except (TypeError, ValueError):
            return False
        for param in sig.parameters.values():
            if param.kind is param.VAR_POSITIONAL:
                return True
        capacity = sum(
            1
            for p in sig.parameters.values()
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        )
        return capacity >= 3

    def _call_handler(self, handler: Callable[..., Any], event: str, payload: Any) -> Any:
        cached = self._arity_cache.get(id(handler))
        if cached is None:
            cached = self._accepts_context(handler)
            self._arity_cache[id(handler)] = cached
        if cached:
            return handler(event, payload, self._make_context())
        return handler(event, payload)

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
                result = self._call_handler(handler, event, payload)
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

            extra = {k: v for k, v in result.items() if k not in (_BLOCK, _ARGS, _OUTPUT)}
            merged.update(extra)
            # Write modifications back so later handlers chain off them.
            payload.update(extra)

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
                result = self._call_handler(handler, event, payload)
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

            extra = {k: v for k, v in result.items() if k not in (_BLOCK, _ARGS, _OUTPUT)}
            merged.update(extra)
            # Write modifications back so later handlers chain off them.
            payload.update(extra)

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
# Extension Runner
# =============================================================================


class ExtensionRunner:
    """Coordinates loaded extensions, binds runtime actions, and creates contexts.

    Mirrors pi's ``ExtensionRunner``: owns the shared runtime, lazily binds
    core/command/UI actions, tracks stale instances, and provides special emit
    methods for chained events.
    """

    def __init__(self, extensions: list[Extension], runtime: ExtensionRuntime, cwd: str) -> None:
        self._extensions = extensions
        self._runtime = runtime
        self._cwd = cwd
        self._ui_context: ExtensionUIContext = _NO_OP_UI_CONTEXT
        self._mode = "print"
        self._stale_message: str | None = None
        self._error_listeners: set[Callable[[ExtensionError], None]] = set()

    # ---- lifecycle -------------------------------------------------------

    def bind_core(
        self,
        actions: ExtensionActions,
        context_actions: ExtensionContextActions,
        provider_actions: ExtensionProviderActions | None = None,
    ) -> None:
        self._runtime.send_message = actions.send_message
        self._runtime.send_user_message = actions.send_user_message
        self._runtime.append_entry = actions.append_entry
        self._runtime.set_session_name = actions.set_session_name
        self._runtime.get_session_name = actions.get_session_name
        self._runtime.set_label = actions.set_label
        self._runtime.get_active_tools = actions.get_active_tools
        self._runtime.get_all_tools = actions.get_all_tools
        self._runtime.set_active_tools = actions.set_active_tools
        self._runtime.refresh_tools = actions.refresh_tools
        self._runtime.get_commands = actions.get_commands
        self._runtime.set_model = actions.set_model
        self._runtime.get_thinking_level = actions.get_thinking_level
        self._runtime.set_thinking_level = actions.set_thinking_level

        self._get_model = context_actions.get_model
        self._get_scoped_models = context_actions.get_scoped_models
        self._is_idle = context_actions.is_idle
        self._is_project_trusted = context_actions.is_project_trusted
        self._get_signal = context_actions.get_signal
        self._abort = context_actions.abort
        self._has_pending_messages = context_actions.has_pending_messages
        self._shutdown = context_actions.shutdown
        self._get_context_usage = context_actions.get_context_usage
        self._compact = context_actions.compact
        self._get_system_prompt = context_actions.get_system_prompt
        self._get_system_prompt_options = getattr(
            context_actions, "get_system_prompt_options", lambda: {}
        )

        if provider_actions:
            self._runtime.register_provider = provider_actions.register_provider
            self._runtime.register_native_provider = provider_actions.register_native_provider
            self._runtime.unregister_provider = provider_actions.unregister_provider

        for pending in self._runtime.pending_provider_registrations:
            try:
                if provider_actions and provider_actions.register_provider:
                    provider_actions.register_provider(pending.name, pending.config)
            except Exception as exc:
                self._emit_error(pending.extension_path, "register_provider", exc)
        self._runtime.pending_provider_registrations.clear()
        for pending in self._runtime.pending_native_provider_registrations:
            try:
                if provider_actions and provider_actions.register_native_provider:
                    provider_actions.register_native_provider(pending.provider)
            except Exception as exc:
                self._emit_error(pending.extension_path, "register_provider", exc)
        self._runtime.pending_native_provider_registrations.clear()

    def bind_command_context(self, actions: ExtensionCommandContextActions | None = None) -> None:
        if actions:
            self._wait_for_idle = actions.wait_for_idle
            self._new_session = actions.new_session
            self._fork = actions.fork
            self._navigate_tree = actions.navigate_tree
            self._switch_session = actions.switch_session
            self._reload = actions.reload
            return
        self._wait_for_idle = lambda: asyncio.ensure_future(asyncio.sleep(0))
        self._new_session = lambda *a, **kw: asyncio.ensure_future(asyncio.sleep(0))
        self._fork = lambda *a, **kw: asyncio.ensure_future(asyncio.sleep(0))
        self._navigate_tree = lambda *a, **kw: asyncio.ensure_future(asyncio.sleep(0))
        self._switch_session = lambda *a, **kw: asyncio.ensure_future(asyncio.sleep(0))
        self._reload = lambda: asyncio.ensure_future(asyncio.sleep(0))

    def set_ui_context(
        self, ui_context: ExtensionUIContext | None = None, mode: str = "print"
    ) -> None:
        self._ui_context = ui_context or _NO_OP_UI_CONTEXT
        self._mode = mode

    def get_ui_context(self) -> ExtensionUIContext:
        return self._ui_context

    def has_ui(self) -> bool:
        return self._ui_context is not _NO_OP_UI_CONTEXT

    # ---- stale tracking --------------------------------------------------

    def invalidate(self, message: str | None = None) -> None:
        if self._stale_message:
            return
        self._stale_message = message or (
            "This extension ctx is stale after session replacement or reload. "
            "Do not use a captured pi or command ctx after ctx.newSession(), "
            "ctx.fork(), ctx.switchSession(), or ctx.reload(). For newSession, "
            "fork, and switchSession, move post-replacement work into withSession "
            "and use the ctx passed to withSession. For reload, do not use the old "
            "ctx after await ctx.reload()."
        )
        self._runtime.invalidate()

    def _assert_active(self) -> None:
        if self._stale_message:
            raise RuntimeError(self._stale_message)

    # ---- context creation ------------------------------------------------

    def create_context(self) -> Any:
        runner = self

        class _ExtensionContext:
            @property
            def ui(self) -> ExtensionUIContext:
                runner._assert_active()
                return runner._ui_context

            @property
            def mode(self) -> str:
                runner._assert_active()
                return runner._mode

            @property
            def hasUI(self) -> bool:  # noqa: N802
                runner._assert_active()
                return runner.has_ui()

            @property
            def cwd(self) -> str:
                runner._assert_active()
                return runner._cwd

            @property
            def session_manager(self) -> Any:
                runner._assert_active()
                return runner._runtime.session_manager

            @property
            def model_registry(self) -> Any:
                runner._assert_active()
                return runner._runtime.model_registry

            @property
            def model(self) -> Any:
                runner._assert_active()
                return runner._get_model()

            @property
            def scoped_models(self) -> list[Any]:
                runner._assert_active()
                return runner._get_scoped_models()

            @property
            def thinking_level(self) -> str | None:
                runner._assert_active()
                return runner._runtime.get_thinking_level()

            def isIdle(self) -> bool:  # noqa: N802
                runner._assert_active()
                return runner._is_idle()

            def isProjectTrusted(self) -> bool:  # noqa: N802
                runner._assert_active()
                return runner._is_project_trusted()

            @property
            def signal(self) -> Any:
                runner._assert_active()
                return runner._get_signal()

            def abort(self) -> None:
                runner._assert_active()
                runner._abort()

            def hasPendingMessages(self) -> bool:  # noqa: N802
                runner._assert_active()
                return runner._has_pending_messages()

            def shutdown(self) -> None:
                runner._assert_active()
                runner._shutdown()

            def getContextUsage(self) -> Any:  # noqa: N802
                runner._assert_active()
                return runner._get_context_usage()

            def compact(self, options: dict[str, Any] | None = None) -> None:
                runner._assert_active()
                runner._compact(options)

            def getSystemPrompt(self) -> str:  # noqa: N802
                runner._assert_active()
                return runner._get_system_prompt()

        return _ExtensionContext()

    def create_command_context(self) -> Any:
        ctx = self.create_context()

        class _ExtensionCommandContext(ctx.__class__):  # type: ignore[valid-type,misc]
            def getSystemPromptOptions(self) -> dict[str, Any]:  # noqa: N802
                self._assert_active()  # type: ignore[attr-defined]
                return self._get_system_prompt_options()  # type: ignore[attr-defined]

            async def waitForIdle(self) -> None:  # noqa: N802
                self._assert_active()  # type: ignore[attr-defined]
                return await self._wait_for_idle()  # type: ignore[attr-defined]

            async def newSession(self, options: dict[str, Any] | None = None) -> dict[str, Any]:  # noqa: N802
                self._assert_active()  # type: ignore[attr-defined]
                return await self._new_session(options)  # type: ignore[attr-defined]

            async def fork(
                self, entry_id: str, options: dict[str, Any] | None = None
            ) -> dict[str, Any]:
                self._assert_active()  # type: ignore[attr-defined]
                return await self._fork(entry_id, options)  # type: ignore[attr-defined]

            async def navigateTree(  # noqa: N802
                self, target_id: str, options: dict[str, Any] | None = None
            ) -> dict[str, Any]:
                self._assert_active()  # type: ignore[attr-defined]
                return await self._navigate_tree(target_id, options)  # type: ignore[attr-defined]

            async def switchSession(  # noqa: N802
                self, session_path: str, options: dict[str, Any] | None = None
            ) -> dict[str, Any]:
                self._assert_active()  # type: ignore[attr-defined]
                return await self._switch_session(session_path, options)  # type: ignore[attr-defined]

            async def reload(self) -> None:
                self._assert_active()  # type: ignore[attr-defined]
                return await self._reload()  # type: ignore[attr-defined]

        # Preserve property descriptors from the base context by re-instantiating
        # via the same closure pattern; command-only methods are added above.
        return _ExtensionCommandContext()

    # ---- special emitters ------------------------------------------------

    async def emit_message_end(self, event: MessageEndEvent) -> Any | None:
        ctx = self.create_context()
        current = event.message
        for ext in self._extensions:
            handlers = ext.handlers.get(MessageEndEvent().type, [])
            for handler in handlers:
                try:
                    result = await handler(MessageEndEvent(message=current), ctx)
                    if result and getattr(result, "message", None) is not None:
                        current = result.message
                except Exception as exc:
                    self._emit_error(ext.path, MessageEndEvent().type, exc)
        return current

    async def emit_tool_result(self, event: ToolResultEvent) -> ToolResultEventResult | None:
        ctx = self.create_context()
        modified = False
        current = event
        for ext in self._extensions:
            handlers = ext.handlers.get(ToolResultEvent().type, [])
            for handler in handlers:
                try:
                    result = await handler(current, ctx)
                    if not result:
                        continue
                    if isinstance(result, dict):
                        if result.get("content") is not None:
                            current.content = result["content"]
                            modified = True
                        if result.get("details") is not None:
                            current.details = result["details"]
                            modified = True
                        if result.get("is_error") is not None:
                            current.is_error = result["is_error"]
                            modified = True
                        if result.get("output") is not None:
                            current.output = result["output"]
                            modified = True
                except Exception as exc:
                    self._emit_error(ext.path, ToolResultEvent().type, exc)
        if not modified:
            return None
        return ToolResultEventResult(
            content=current.content,
            details=current.details,
            is_error=current.is_error,
            output=getattr(current, "output", None),
        )

    async def emit_tool_call(self, event: ToolCallEvent) -> ToolCallEventResult | None:
        ctx = self.create_context()
        result: ToolCallEventResult | None = None
        for ext in self._extensions:
            handlers = ext.handlers.get(ToolCallEvent().type, [])
            for handler in handlers:
                try:
                    handler_result = await handler(event, ctx)
                    if handler_result:
                        result = ToolCallEventResult(
                            block=handler_result.get("block", False),
                            reason=handler_result.get("reason"),
                            terminate=handler_result.get("terminate", False),
                            args=handler_result.get("args"),
                        )
                        if result.block:
                            return result
                except Exception as exc:
                    self._emit_error(ext.path, ToolCallEvent().type, exc)
        return result

    async def emit_user_bash(self, event: UserBashEvent) -> UserBashEventResult | None:
        ctx = self.create_context()
        for ext in self._extensions:
            handlers = ext.handlers.get(UserBashEvent().type, [])
            for handler in handlers:
                try:
                    handler_result = await handler(event, ctx)
                    if handler_result:
                        return UserBashEventResult(
                            operations=handler_result.get("operations"),
                            result=handler_result.get("result"),
                        )
                except Exception as exc:
                    self._emit_error(ext.path, UserBashEvent().type, exc)
        return None

    async def emit_context(self, messages: list[Any]) -> list[Any]:
        ctx = self.create_context()
        current = list(messages)
        for ext in self._extensions:
            handlers = ext.handlers.get(ContextEvent().type, [])
            for handler in handlers:
                try:
                    result = await handler(ContextEvent(messages=current), ctx)
                    if result and getattr(result, "messages", None) is not None:
                        current = result.messages
                except Exception as exc:
                    self._emit_error(ext.path, ContextEvent().type, exc)
        return current

    async def emit_before_provider_request(self, payload: Any) -> Any:
        ctx = self.create_context()
        current = payload
        for ext in self._extensions:
            handlers = ext.handlers.get(BeforeProviderRequestEvent().type, [])
            for handler in handlers:
                try:
                    result = await handler(BeforeProviderRequestEvent(payload=current), ctx)
                    if result is not None:
                        current = result
                except Exception as exc:
                    self._emit_error(ext.path, BeforeProviderRequestEvent().type, exc)
        return current

    async def emit_before_provider_headers(self, headers: dict[str, str]) -> dict[str, str]:
        ctx = self.create_context()
        for ext in self._extensions:
            handlers = ext.handlers.get(BeforeProviderHeadersEvent().type, [])
            for handler in handlers:
                try:
                    await handler(BeforeProviderHeadersEvent(headers=headers), ctx)
                except Exception as exc:
                    self._emit_error(ext.path, BeforeProviderHeadersEvent().type, exc)
        return headers

    async def emit_before_agent_start(
        self,
        prompt: str,
        images: list[Any],
        system_prompt: str,
        system_prompt_options: dict[str, Any],
    ) -> BeforeAgentStartEventResult | None:
        ctx = self.create_context()
        current_prompt = system_prompt
        messages: list[dict[str, Any]] = []
        for ext in self._extensions:
            handlers = ext.handlers.get(BeforeAgentStartEvent().type, [])
            for handler in handlers:
                try:
                    result = await handler(
                        BeforeAgentStartEvent(
                            prompt=prompt,
                            images=images,
                            system_prompt=current_prompt,
                            system_prompt_options=system_prompt_options,
                        ),
                        ctx,
                    )
                    if result:
                        if getattr(result, "message", None):
                            messages.append(result.message)
                        if getattr(result, "system_prompt", None) is not None:
                            current_prompt = result.system_prompt
                except Exception as exc:
                    self._emit_error(ext.path, BeforeAgentStartEvent().type, exc)
        if messages or current_prompt != system_prompt:
            return BeforeAgentStartEventResult(
                message=messages[0] if messages else None,
                system_prompt=current_prompt if current_prompt != system_prompt else None,
            )
        return None

    async def emit_resources_discover(self, cwd: str, reason: str) -> ResourcesDiscoverResult:
        ctx = self.create_context()
        skill_paths: list[str] = []
        prompt_paths: list[str] = []
        theme_paths: list[str] = []
        for ext in self._extensions:
            handlers = ext.handlers.get(ResourcesDiscoverEvent().type, [])
            for handler in handlers:
                try:
                    result = await handler(ResourcesDiscoverEvent(cwd=cwd, reason=reason), ctx)
                    if result:
                        if getattr(result, "skill_paths", None):
                            skill_paths.extend(result.skill_paths)
                        if getattr(result, "prompt_paths", None):
                            prompt_paths.extend(result.prompt_paths)
                        if getattr(result, "theme_paths", None):
                            theme_paths.extend(result.theme_paths)
                except Exception as exc:
                    self._emit_error(ext.path, ResourcesDiscoverEvent().type, exc)
        return ResourcesDiscoverResult(
            skill_paths=skill_paths, prompt_paths=prompt_paths, theme_paths=theme_paths
        )

    async def emit_input(
        self,
        text: str,
        source: str,
        images: list[Any] | None = None,
        streaming_behavior: str | None = None,
    ) -> InputEventResult:
        ctx = self.create_context()
        current_text = text
        current_images = list(images or [])
        for ext in self._extensions:
            handlers = ext.handlers.get(InputEvent().type, [])
            for handler in handlers:
                try:
                    result = await handler(
                        InputEvent(
                            text=current_text,
                            images=current_images,
                            source=source,
                            streaming_behavior=streaming_behavior,
                        ),
                        ctx,
                    )
                    if not result:
                        continue
                    action = getattr(result, "action", "continue")
                    if action == "handled":
                        return InputEventResult(action="handled")
                    if action == "transform":
                        current_text = getattr(result, "text", current_text)
                        current_images = getattr(result, "images", current_images)
                except Exception as exc:
                    self._emit_error(ext.path, InputEvent().type, exc)
        if current_text != text or current_images != (images or []):
            return InputEventResult(action="transform", text=current_text, images=current_images)
        return InputEventResult(action="continue")

    # ---- aggregation helpers ---------------------------------------------

    def has_handlers(self, event_type: str) -> bool:
        return any(ext.handlers.get(event_type) for ext in self._extensions)

    def get_message_renderer(self, custom_type: str) -> Callable[..., Any] | None:
        for ext in self._extensions:
            renderer = ext.message_renderers.get(custom_type)
            if renderer:
                return renderer
        return None

    def get_entry_renderer(self, custom_type: str) -> Callable[..., Any] | None:
        for ext in self._extensions:
            renderer = ext.entry_renderers.get(custom_type)
            if renderer:
                return renderer
        return None

    def get_all_tools(self) -> list[BaseTool]:
        seen: set[str] = set()
        tools: list[BaseTool] = []
        for ext in self._extensions:
            for tool in ext.tools.values():
                if tool.name not in seen:
                    seen.add(tool.name)
                    tools.append(tool)
        return tools

    def get_tool_definition(self, tool_name: str) -> Any | None:
        for ext in self._extensions:
            tool = ext.tools.get(tool_name)
            if tool:
                return tool
        return None

    def get_commands(self) -> dict[str, ExtensionCommand]:
        merged: dict[str, ExtensionCommand] = {}
        for ext in self._extensions:
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

    def get_flags(self) -> dict[str, Any]:
        flags: dict[str, Any] = {}
        for ext in self._extensions:
            for flag in ext.flags.values():
                if flag.name not in flags:
                    flags[flag.name] = flag
        return flags

    def get_shortcuts(self) -> dict[str, Any]:
        shortcuts: dict[str, Any] = {}
        for ext in self._extensions:
            for key, shortcut in ext.shortcuts.items():
                if key not in shortcuts:
                    shortcuts[key] = shortcut
        return shortcuts

    # ---- error handling --------------------------------------------------

    def on_error(self, listener: Callable[[ExtensionError], None]) -> Callable[[], None]:
        self._error_listeners.add(listener)
        return lambda: self._error_listeners.discard(listener)

    def _emit_error(self, extension_path: str | Path, event: str, error: Exception) -> None:
        ext_error = ExtensionError(
            extension_path=str(extension_path),
            event=event,
            error=str(error),
            stack=getattr(error, "__traceback__", None),
        )
        for listener in list(self._error_listeners):
            with contextlib.suppress(Exception):
                listener(ext_error)


# =============================================================================
# Extension Runtime
# =============================================================================


class ExtensionRuntime:
    """Shared runtime state for all extensions.

    Action methods are throwing stubs until ``bind_core()`` replaces them.
    """

    def __init__(self) -> None:
        self.send_message: Callable[..., Any] = _not_initialized
        self.send_user_message: Callable[..., Any] = _not_initialized
        self.append_entry: Callable[..., Any] = _not_initialized
        self.set_session_name: Callable[..., Any] = _not_initialized
        self.get_session_name: Callable[..., Any] = _not_initialized
        self.set_label: Callable[..., Any] = _not_initialized
        self.get_active_tools: Callable[..., Any] = _not_initialized
        self.get_all_tools: Callable[..., Any] = _not_initialized
        self.set_active_tools: Callable[..., Any] = _not_initialized
        self.refresh_tools: Callable[..., Any] = lambda: None
        self.get_commands: Callable[..., Any] = _not_initialized
        self.set_model: Callable[..., Any] = _not_initialized
        self.get_thinking_level: Callable[..., Any] = _not_initialized
        self.set_thinking_level: Callable[..., Any] = _not_initialized
        self.register_provider: Callable[..., Any] = _not_initialized
        self.register_native_provider: Callable[..., Any] = _not_initialized
        self.unregister_provider: Callable[..., Any] = _not_initialized
        self.flag_values: dict[str, Any] = {}
        self.pending_provider_registrations: list[Any] = []
        self.pending_native_provider_registrations: list[Any] = []
        self.session_manager: Any = None
        self.model_registry: Any = None

    def invalidate(self) -> None:
        pass


def _not_initialized(*args: Any, **kwargs: Any) -> Any:
    raise RuntimeError(
        "Extension runtime not initialized. Action methods cannot be called "
        "during extension loading."
    )


# =============================================================================
# UI Context
# =============================================================================


class ExtensionUIContext:
    """UI surface for extensions, mirroring pi's ``ctx.ui``.

    The real TUI-backed implementation (:class:`vtx.tui.extension_ui.
    TextualExtensionUI`) is installed by the host via
    ``EventBus.set_ui_context``; this base class provides safe no-op
    fallbacks so extensions can call UI methods without mode checks.

    Dialog primitives (``confirm``, ``select``, ``input``) are coroutines so
    the same ``await ctx.ui.confirm(...)`` code runs in every mode: TUI mode
    shows a real modal dialog, headless/print mode resolves immediately to
    safe defaults (``False`` / ``None`` / ``None``). Handlers must therefore
    be ``async def`` to use them.

    All dialog primitives accept two optional cancellation knobs:

    - ``timeout``: seconds; on expiry the dialog is dismissed and the safe
      default returned.
    - ``signal``: an ``asyncio.Event``-like object whose ``wait()`` resolves
      the dialog with its default (used for abort-aware handlers).
    """

    async def select(
        self, title: str, options: list[str], *, timeout: float | None = None, signal: Any = None
    ) -> str | None:
        """Ask the user to pick one of ``options``; returns the choice or ``None``."""
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
        """Show a custom UI component modally; returns whatever it dismisses with.

        ``component`` may be a pre-built Textual widget, a widget class
        (instantiated with no args), a ``ModalScreen`` subclass/instance, or a
        zero-argument callable returning either. Inside the component call
        ``self.screen.dismiss(result)`` to hand a value back to the awaiting
        handler; Escape dismisses with ``None``.
        """
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


_ExtensionUIContext = ExtensionUIContext


class _NoOpUIContext(ExtensionUIContext):
    pass


_NO_OP_UI_CONTEXT = _NoOpUIContext()


# =============================================================================
# Helper dataclasses for runner actions
# =============================================================================


@dataclass
class ExtensionActions:
    send_message: Callable[..., Any]
    send_user_message: Callable[..., Any]
    append_entry: Callable[..., Any]
    set_session_name: Callable[..., Any]
    get_session_name: Callable[..., Any]
    set_label: Callable[..., Any]
    get_active_tools: Callable[..., Any]
    get_all_tools: Callable[..., Any]
    set_active_tools: Callable[..., Any]
    refresh_tools: Callable[..., Any]
    get_commands: Callable[..., Any]
    set_model: Callable[..., Any]
    get_thinking_level: Callable[..., Any]
    set_thinking_level: Callable[..., Any]


@dataclass
class ExtensionContextActions:
    get_model: Callable[[], Any]
    get_scoped_models: Callable[[], list[Any]]
    is_idle: Callable[[], bool]
    is_project_trusted: Callable[[], bool]
    get_signal: Callable[[], Any]
    abort: Callable[[], None]
    has_pending_messages: Callable[[], bool]
    shutdown: Callable[[], None]
    get_context_usage: Callable[[], Any]
    compact: Callable[[Any], None]
    get_system_prompt: Callable[[], str]
    get_system_prompt_options: Callable[[], dict[str, Any]] = lambda: {}


@dataclass
class ExtensionCommandContextActions:
    wait_for_idle: Callable[[], Any]
    new_session: Callable[..., Any]
    fork: Callable[..., Any]
    navigate_tree: Callable[..., Any]
    switch_session: Callable[..., Any]
    reload: Callable[[], Any]


@dataclass
class ExtensionProviderActions:
    register_provider: Callable[..., Any]
    register_native_provider: Callable[..., Any]
    unregister_provider: Callable[..., Any]


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
        runner: ExtensionRunner | None = None,
    ) -> None:
        self._extension = extension
        self._bus = bus
        self.cwd = cwd
        self.session_file = session_file
        self.config_dir = config_dir
        self._runner = runner

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
                self._extension.handlers.setdefault(event, []).append(fn)
                return fn

            return decorator
        self._bus.on(event, handler)
        self._extension.handler_calls[_qualname(handler)] = 0
        self._extension.handlers.setdefault(event, []).append(handler)
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
        ui_block: type | None = None,
        tool_icon: str | None = None,
    ) -> BaseTool:
        """Register a new LLM-callable tool, or override a built-in.

        ``parameters`` is a JSON Schema object (the same shape providers
        receive). The pydantic model used by the agent loop is generated from
        it, so extensions can use any JSON-Schema-compliant type.

        ``execute`` is called with ``(args_dict, ctx_dict)`` and may be sync
        or async. It must return a :class:`ToolResult`-like object (a dict
        with the same keys also works).

        ``ui_block`` is an optional Textual widget class (a subclass of
        :class:`vtx.ui.blocks.ToolBlock`) that the TUI instantiates instead
        of the default block. Use it to ship a custom header, approval
        prompt, or result rendering for a domain-specific tool. The chat
        log sets ``block.tool`` to this tool after construction so the
        custom block can introspect it.

        ``tool_icon`` overrides the default ``"↪"`` extension icon. Useful
        when shipping a built-in-feeling tool (e.g. the bundled ``task``
        extension uses ``"⊕"``) without forking ``ExtensionTool``.
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
            ui_block=ui_block,
        )
        if tool_icon is not None:
            tool.tool_icon = tool_icon
        self._extension.tools[name] = tool
        return tool

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
    ) -> BaseTool:
        """Register a tool that only exists when ``agent`` is the active agent.

        This is the cross-agent variant of :meth:`register_tool` — for
        extensions (not agent files) that want to contribute a tool to a
        specific agent. The runtime surfaces the tool only when the named
        agent is the currently-active one.

        ``agent`` is the agent's ``name`` (must match the file stem or
        package directory name of a ``.vtx/agent/<name>.py`` file).
        """
        if not agent or not isinstance(agent, str):
            raise ValueError("Agent name must be a non-empty string")
        if not name or not isinstance(name, str):
            raise ValueError("Tool name must be a non-empty string")
        params_model = _json_schema_to_pydantic(name, parameters)
        tool = ExtensionTool(
            name=name,
            description=description,
            parameters=parameters,
            params_model=params_model,
            execute_fn=execute,
            owner=f"{self._extension.name}→{agent}",
            mutating=mutating,
            label=label or name,
            ui_block=ui_block,
        )
        bucket = self._extension.local_tools.setdefault(agent, {})
        bucket[name] = tool
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

        In TUI mode this routes through the installed UI context (rendered in
        the chat log). In headless mode it falls back to the log.
        """
        ui = getattr(self._bus, "_ui_context", None)
        if ui is not None:
            ui.notify(f"{self._extension.name}: {message}", level)
            return
        match level:
            case "info":
                log.info(f"{self._extension.name}: {message}")
            case "warning":
                log.warning(f"{self._extension.name}: {message}")
            case "error":
                log.error(f"{self._extension.name}: {message}")
            case _:
                log.info(f"{self._extension.name}: {message}")

    # ---- shortcuts and flags ---------------------------------------------

    def register_shortcut(self, shortcut: str, options: dict[str, Any]) -> None:
        """Register a keyboard shortcut."""
        from vtx.ai.agent.extensions import ExtensionShortcut

        self._extension.shortcuts[shortcut] = ExtensionShortcut(
            shortcut=shortcut,
            description=options.get("description"),
            handler=options.get("handler"),
            extension_path=str(self._extension.path),
        )

    def register_flag(self, name: str, options: dict[str, Any]) -> None:
        """Register a CLI flag."""
        from vtx.ai.agent.extensions import ExtensionFlag

        self._extension.flags[name] = ExtensionFlag(
            name=name,
            description=options.get("description"),
            type=options.get("type", "boolean"),
            default=options.get("default"),
            extension_path=str(self._extension.path),
        )

    def get_flag(self, name: str) -> Any:
        """Get the current value of a registered flag."""
        if not self._runner:
            flag = self._extension.flags.get(name)
            return flag.default if flag is not None else None
        return self._runner._runtime.flag_values.get(name)

    # ---- message / entry rendering ---------------------------------------

    def register_message_renderer(self, custom_type: str, renderer: Callable[..., Any]) -> None:
        """Register a custom renderer for messages with ``customType``."""
        self._extension.message_renderers[custom_type] = renderer

    def register_entry_renderer(self, custom_type: str, renderer: Callable[..., Any]) -> None:
        """Register a custom renderer for TUI-only entries."""
        self._extension.entry_renderers[custom_type] = renderer

    # ---- actions ---------------------------------------------------------

    def send_message(self, message: dict[str, Any], options: dict[str, Any] | None = None) -> None:
        if self._runner:
            self._runner._runtime.send_message(message, options)

    def send_user_message(
        self, content: str | list[Any], options: dict[str, Any] | None = None
    ) -> None:
        if self._runner:
            self._runner._runtime.send_user_message(content, options)

    def append_entry(self, custom_type: str, data: Any = None) -> None:
        if self._runner:
            self._runner._runtime.append_entry(custom_type, data)

    def set_session_name(self, name: str) -> None:
        if self._runner:
            self._runner._runtime.set_session_name(name)

    def get_session_name(self) -> str | None:
        if self._runner:
            return self._runner._runtime.get_session_name()
        return None

    def set_label(self, entry_id: str, label: str | None) -> None:
        if self._runner:
            self._runner._runtime.set_label(entry_id, label)

    def exec(self, command: str, args: list[str], options: dict[str, Any] | None = None) -> Any:
        import subprocess

        cwd = options.get("cwd") if options else None
        try:
            result = subprocess.run(
                [command, *args],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=options.get("timeout") if options else None,
            )
            return {"code": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
        except Exception as exc:
            return {"code": -1, "stdout": "", "stderr": str(exc)}

    def get_active_tools(self) -> list[str]:
        if self._runner:
            return self._runner._runtime.get_active_tools()
        return []

    def get_all_tools(self) -> list[dict[str, Any]]:
        if self._runner:
            return self._runner._runtime.get_all_tools()
        return []

    def set_active_tools(self, tool_names: list[str]) -> None:
        if self._runner:
            self._runner._runtime.set_active_tools(tool_names)

    def set_model(self, model: Any) -> bool:
        if self._runner:
            return self._runner._runtime.set_model(model)
        return False

    def get_thinking_level(self) -> str | None:
        if self._runner:
            return self._runner._runtime.get_thinking_level()
        return None

    def set_thinking_level(self, level: str) -> None:
        if self._runner:
            self._runner._runtime.set_thinking_level(level)

    # ---- provider registration -------------------------------------------

    def register_provider(
        self, provider_or_name: Any, config: dict[str, Any] | None = None
    ) -> None:
        if self._runner:
            if isinstance(provider_or_name, str):
                self._runner._runtime.pending_provider_registrations.append(
                    ExtensionProviderRegistration(name=provider_or_name, config=config or {})
                )
            else:
                self._runner._runtime.pending_native_provider_registrations.append(
                    ExtensionNativeProviderRegistration(provider=provider_or_name)
                )

    def unregister_provider(self, name: str) -> None:
        if self._runner:
            self._runner._runtime.pending_provider_registrations = [
                p for p in self._runner._runtime.pending_provider_registrations if p.name != name
            ]

    # ---- inter-extension events ------------------------------------------

    @property
    def events(self) -> Any:
        return self._bus

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

    def on_input(self, handler: Callable[..., Any]) -> Callable[..., Any]:
        return self.on(INPUT, handler)

    def on_before_agent_start(self, handler: Callable[..., Any]) -> Callable[..., Any]:
        return self.on(BEFORE_AGENT_START, handler)

    def on_model_select(self, handler: Callable[..., Any]) -> Callable[..., Any]:
        return self.on(MODEL_SELECT, handler)

    def on_user_bash(self, handler: Callable[..., Any]) -> Callable[..., Any]:
        return self.on(USER_BASH, handler)

    def on_before_provider_headers(self, handler: Callable[..., Any]) -> Callable[..., Any]:
        """Mutate outgoing HTTP headers: ``(event, payload[, ctx]) -> None``.

        ``payload["headers"]`` maps header name to value; set a key to
        ``None`` to delete it. Fired once per request; retries reuse the
        prepared headers.
        """
        return self.on(BEFORE_PROVIDER_HEADERS, handler)

    def on_before_provider_request(self, handler: Callable[..., Any]) -> Callable[..., Any]:
        """Inspect/rewrite the wire payload: return ``{"payload": {...}}`` to replace.

        Runs after the payload is fully built, right before the request is
        sent. Later handlers chain off earlier replacements.
        """
        return self.on(BEFORE_PROVIDER_REQUEST, handler)


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
        ui_block: type | None = None,
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
        self.ui_block = ui_block

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
    path: Path,
    *,
    bus: EventBus,
    cwd: str,
    session_file: str | None,
    config_dir: Path,
    runner: ExtensionRunner | None = None,
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
    api = ExtensionAPI(
        extension, bus, cwd=cwd, session_file=session_file, config_dir=config_dir, runner=runner
    )
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
    runner: ExtensionRunner | None = None,
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
                    runner=runner,
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
    runner: ExtensionRunner | None = field(default=None)

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

    def local_tools_for(self, agent_name: str) -> list[BaseTool]:
        """Per-agent local tools contributed by session extensions.

        Aggregates ``api.register_local_tool(agent=...)`` registrations
        across all extensions for the given agent name. Returns an empty
        list if no extension contributed to that agent.
        """
        out: list[BaseTool] = []
        for ext in self.extensions:
            bucket = ext.local_tools.get(agent_name)
            if not bucket:
                continue
            for name, tool in bucket.items():
                if any(t.name == name for t in out):
                    continue
                out.append(tool)
        return out

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

    vtx ships NO bundled extensions of its own — every extension is
    third-party-style. If you want the ``Task`` sub-agent dispatcher,
    copy ``examples/extensions/task_tool.py`` into your
    ``~/.vtx/agent/extensions/`` directory (or your project's
    ``.vtx/extensions/``).
    """
    configured: list[str] = []
    if auto_discover:
        from vtx.coding_agent.config import config as vtx_config

        configured.extend(vtx_config.extensions)
    if extra_paths:
        configured.extend(extra_paths)

    runtime = ExtensionRuntime()
    runner = ExtensionRunner([], runtime, cwd)

    if auto_discover:
        exts, errors, bus = load_all_extensions(
            cwd=cwd, configured=configured, session_file=session_file, runner=runner
        )
    else:
        # Only honor explicit paths when discovery is off.
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
                                runner=runner,
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
                            runner=runner,
                        )
                    )
                except ExtensionLoadError as exc:
                    errors.append(str(exc))

    runner._extensions = exts
    loaded = LoadedExtensions(extensions=exts, errors=errors, bus=bus, runner=runner)
    install_provider_bridge(bus)
    return loaded


# =============================================================================
# Provider request hook bridge
# =============================================================================

_PROVIDER_BRIDGE_BUS: EventBus | None = None


def install_provider_bridge(bus: EventBus) -> None:
    """Expose provider-level hook points to extensions.

    Wires the transport-level registry (:mod:`vtx.ai.provider_hooks`) to the
    bus so ``before_provider_headers`` / ``before_provider_request``
    handlers run with full ``ctx`` support right before an LLM request is
    sent. Idempotent: only the first bus in a process is bridged (matching
    the single active runtime).

    Called automatically by :func:`load_for_runtime`.
    """
    global _PROVIDER_BRIDGE_BUS
    if _PROVIDER_BRIDGE_BUS is not None:
        return
    _PROVIDER_BRIDGE_BUS = bus

    from vtx.ai import provider_hooks

    async def _on_headers(headers: dict[str, str | None], context: dict[str, Any]) -> None:
        await bus.emit(
            BEFORE_PROVIDER_HEADERS,
            provider=context.get("provider", ""),
            model=context.get("model", ""),
            headers=headers,
        )

    async def _on_request(payload: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        merged = await bus.emit(
            BEFORE_PROVIDER_REQUEST,
            provider=context.get("provider", ""),
            model=context.get("model", ""),
            payload=payload,
        )
        # Handlers either mutate ``payload["payload"]`` in place (already
        # visible here, since we pass the same object) or return
        # ``{"payload": {...}}`` (written back by the bus for chaining).
        # Re-wrap for the registry's ``{"payload": ...}`` return convention.
        result = merged.get("payload")
        return {"payload": result if isinstance(result, dict) else payload}

    provider_hooks.register_headers_listener(_on_headers)
    provider_hooks.register_request_listener(_on_request)


# Suppress "imported but unused" for the typing-only imports above.
_ = (get_args, ImageContent, TextContent, Field)

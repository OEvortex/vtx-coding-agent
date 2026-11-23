"""``ctx.sdk`` — the unified public surface for the whole ctx harness.

One namespace for every layer, so anything ctx can do is reachable from a
single import:

* **Standalone runs** — ``Agent`` / ``Runner`` / ``@tool`` / ``define_tool`` /
  sessions / guardrails / handoffs / permissions / tracing
  (re-exported from :mod:`ctx.agent.sdk`).
* **Wire types** — messages, stream parts, ``ToolResult``,
  ``ToolDefinition`` (re-exported from :mod:`ctx.protocol`).
* **The live composition** — :func:`context` returns the running
  composition's ``Context`` (the same object a plugin row gets) and
  :func:`api` returns an ``ExtensionAPI`` (the same object an extension's
  ``register(api)`` gets). ``on()``, ``register_tool()``,
  ``register_command()``, ``register_provider()``, and ``emit()`` are thin
  wrappers over ``api()``/``context()`` for the common cases.
* **Composition control** — ``plugin``/``PluginHost``/``load_profile``/
  ``boot`` for mounting a composition yourself.
* **Providers** — ``BaseProvider`` and the model catalog, resolved lazily
  from :mod:`ctx.ai`.

Nothing here is a second implementation: ``ctx.sdk`` re-exports the real
objects, so a tool built with ``define_tool`` is the same ``BaseTool`` a
plugin row registers and an ``Agent`` runs.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ctx.agent.sdk import (
    Agent,
    AgentOutputSchema,
    AllowlistApprove,
    ApprovalDecision,
    AutoApprove,
    ConsoleTraceProcessor,
    FunctionTool,
    GuardrailFunctionOutput,
    Handoff,
    HandoffCallItem,
    HandoffInputData,
    HandoffOutputItem,
    InMemorySession,
    InputGuardrail,
    InputGuardrailTripwireTriggered,
    JSONLSession,
    JSONLTraceProcessor,
    MessageOutputItem,
    OutputGuardrail,
    OutputGuardrailTripwireTriggered,
    PermissionDecision,
    PermissionPolicy,
    PromptApprove,
    ReasoningItem,
    RunConfig,
    RunItem,
    Runner,
    RunResult,
    RunState,
    RunStreamed,
    SchemaTool,
    Session,
    SessionSettings,
    Span,
    ToolApprovalItem,
    ToolCallItem,
    ToolCallOutputItem,
    ToolGuardrailFunctionOutput,
    ToolInputGuardrailTripwireTriggered,
    ToolOutputGuardrailTripwireTriggered,
    Trace,
    TraceProcessor,
    Usage,
    _ToolApprovalItem,
    add_trace_processor,
    define_tool,
    disable_tracing,
    enable_tracing,
    handoff,
    input_guardrail,
    output_guardrail,
    span,
    tool,
    tool_input_guardrail,
    tool_output_guardrail,
    trace,
)
from ctx.agent.tools.base import BaseTool
from ctx.plugins import (
    DEFAULT_PROFILE,
    STANDARD_KEYS,
    ConfigError,
    Context,
    CycleError,
    DispatchMode,
    DuplicateRowError,
    EventHub,
    EventModeError,
    PatchError,
    Plugin,
    PluginError,
    PluginHost,
    Row,
    RowError,
    Service,
    ServiceMissingError,
    active_context,
    active_host,
    apply_patch,
    apply_plugin,
    as_plugin,
    boot,
    compose,
    dump_config,
    load_profile,
    load_yaml,
    load_yaml_file,
    mount_tool,
    plugin,
    profile_dir,
    profiles_dir,
    reset,
    resolve_plugin,
    rows_from_layer,
    shutdown,
    validate_config,
)
from ctx.plugins import event_contract as events
from ctx.plugins.api import (
    NO_OP_UI_CONTEXT,
    CommandOutcome,
    ExtensionActions,
    ExtensionAPI,
    ExtensionCommand,
    ExtensionError,
    ExtensionFlag,
    ExtensionNativeProviderRegistration,
    ExtensionProviderRegistration,
    ExtensionRecord,
    ExtensionShortcut,
    ExtensionTool,
    ExtensionUIContext,
    blocking_result,
)
from ctx.plugins.event_contract import ALL_EVENTS
from ctx.protocol import (
    AssistantMessage,
    ConstrainedSampling,
    FileChanges,
    ImageContent,
    Message,
    StopReason,
    StreamDone,
    StreamError,
    StreamPart,
    TextContent,
    TextPart,
    ThinkingContent,
    ThinkPart,
    ToolCall,
    ToolCallDelta,
    ToolCallEnd,
    ToolCallStart,
    ToolDefinition,
    ToolParameter,
    ToolResult,
    ToolResultMessage,
    UserMessage,
    format_error,
)

if TYPE_CHECKING:
    from ctx.ai.base import BaseProvider, LLMStream, ProviderConfig
    from ctx.ai.models import ApiType, Model
    from ctx.ai.provider_catalog import ProviderInfo

# ---------------------------------------------------------------------------
# Lazy re-exports: provider/model catalog and headless entry points. These pull
# in ``ctx.ai`` / ``ctx.coding_agent``, so they resolve on first access rather
# than at ``import ctx.sdk`` time — same trick ``ctx`` and ``ctx.ai`` use.
# ---------------------------------------------------------------------------

_LAZY_SOURCES = ("ctx.ai", "ctx.coding_agent")


def __getattr__(name: str) -> Any:
    for module_name in _LAZY_SOURCES:
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            # ``ctx.coding_agent`` ships in ctx-code; skip it when absent.
            if exc.name != module_name:
                raise
            continue
        if (value := getattr(module, name, None)) is not None:
            globals()[name] = value
            return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(__all__)


# ---------------------------------------------------------------------------
# Live-composition access
# ---------------------------------------------------------------------------

_api_cache: dict[int, ExtensionAPI] = {}
_SDK_RECORD_PATH = Path("sdk")


def context() -> Context:
    """Return the running composition's ``Context``.

    This is the same object a plugin row receives: ``ctx.tools``,
    ``ctx.llm``, ``ctx.sessions``, ``ctx.agents``, ``ctx.agent_loop``,
    ``ctx.permissions``, ``ctx.mcp``, ``ctx.skills``, ``ctx.telemetry``,
    ``ctx.commands``, ``ctx.hub``/``ctx.on``/``ctx.emit``, plus
    ``ctx.effect`` for reversible teardown. Raises ``RuntimeError`` when no
    composition is running — use :func:`boot` or :class:`PluginHost` to mount
    one yourself.
    """
    ctx = active_context()
    if ctx is None:
        raise RuntimeError(
            "no ctx composition is running. ctx.sdk context/api/on/register_* "
            "act on the live composition; outside one, use Agent/Runner for "
            "standalone runs or boot()/PluginHost to mount a composition."
        )
    return ctx


def api() -> ExtensionAPI:
    """Return an ``ExtensionAPI`` bound to the running composition.

    This is the same object an extension's ``register(api)`` receives, so
    everything an extension can do — ``register_tool``, ``register_command``,
    ``register_provider``, ``on``, ``send_message``, ``set_model``,
    ``get_active_tools``, ``ui``, ``exec``, flags, renderers, shortcuts — is
    available here too.
    """
    ctx = context()
    cached = _api_cache.get(id(ctx))
    if cached is None or cached._ctx is not ctx:
        cached = ExtensionAPI(
            "sdk", ctx, ExtensionRecord("sdk", _SDK_RECORD_PATH), ExtensionActions()
        )
        _api_cache[id(ctx)] = cached
    return cached


def on(event: str, handler: Callable[..., Any] | None = None) -> Any:
    """Subscribe ``handler`` to a ctx lifecycle event.

    Same contract as ``api.on`` in an extension: the handler may be written
    ``(event, payload)`` or ``(event, payload, ctx)``, receives a typed event
    object for known events, and blocking events may return
    ``{"block": True, "reason": ...}`` / ``{"args": ...}`` / ``{"output": ...}``.
    Works as a plain call or a decorator. Event names are listed in
    ``ctx.sdk.events.ALL_EVENTS``.
    """
    return api().on(event, handler)


async def emit(name: str, *args: Any, **kwargs: Any) -> Any:
    """Emit ``name`` on the composition's event hub and await dispatch."""
    return await context().hub.emit(name, *args, **kwargs)


def emit_nowait(name: str, *args: Any, **kwargs: Any) -> Any:
    """Schedule :func:`emit` on the running loop; returns the task or ``None``."""
    return context().hub.emit_nowait(name, *args, **kwargs)


def register_tool(
    tool_obj: Any = None,
    *,
    name: str | None = None,
    description: str | None = None,
    parameters: dict[str, Any] | None = None,
    execute: Callable[[dict[str, Any], dict[str, Any] | None], Any] | None = None,
    mutating: bool = True,
    needs_approval: bool = False,
    **kwargs: Any,
) -> Any:
    """Register an LLM-callable tool on the running composition.

    Every tool spelling ctx understands works here:

    * ``register_tool(my_tool)`` — any ``BaseTool`` (``@tool``,
      ``define_tool``, a subclass, an ``ExtensionTool``).
    * ``register_tool(fn)`` — a plain callable, wrapped by ``@tool``.
    * ``register_tool(name=..., description=..., parameters={...},
      execute=fn)`` — the schema-driven form, built via ``define_tool``.
    """
    if tool_obj is None:
        if name is None or parameters is None or execute is None:
            raise ValueError(
                "register_tool needs a tool/callable, or "
                "name=, description=, parameters=, execute="
            )
        tool_obj = define_tool(
            name,
            description or "",
            parameters,
            execute=execute,
            mutating=mutating,
            needs_approval=needs_approval,
            **kwargs,
        )
    elif isinstance(tool_obj, BaseTool):
        pass
    elif isinstance(tool_obj, type):
        if not issubclass(tool_obj, BaseTool):
            raise TypeError(
                "register_tool expects a BaseTool, a BaseTool class, a callable, "
                f"or name=/parameters=/execute= kwargs; got class {tool_obj.__name__}"
            )
        tool_obj = tool_obj()
    elif callable(tool_obj):
        from ctx.agent.sdk.tools import tool as _tool_decorator

        tool_obj = _tool_decorator(tool_obj)
    else:
        raise TypeError(
            "register_tool expects a BaseTool, a BaseTool class, a callable, "
            f"or name=/parameters=/execute= kwargs; got {type(tool_obj).__name__}"
        )
    return api().register_tool(tool_obj)


def register_command(
    name: str, description: str | Any = "", handler: Callable[..., Any] | None = None
) -> None:
    """Register a slash command on the running composition.

    ``register_command("name", "description", handler)`` or the decorator
    form ``register_command("name", CommandDef(...))`` — same spellings as
    ``api.register_command``. ``register_command("name", handler)`` also works.
    """
    if handler is None and callable(description) and not isinstance(description, str):
        handler, description = description, ""
    api().register_command(name, description, handler)


def register_provider(provider_or_name: Any, config: dict[str, Any] | None = None) -> None:
    """Register an LLM provider on the running composition.

    ``provider_or_name`` is either a provider name (with ``config``) or a
    ``BaseProvider`` instance — same contract as ``api.register_provider``.
    """
    api().register_provider(provider_or_name, config)


def unregister_provider(name: str) -> None:
    """Remove a provider registered through :func:`register_provider`."""
    api().unregister_provider(name)


__all__ = [  # noqa: RUF022 - grouped by layer for readability
    # agent SDK
    "Agent",
    "AgentOutputSchema",
    "AllowlistApprove",
    "ApprovalDecision",
    "AutoApprove",
    "ConsoleTraceProcessor",
    "FunctionTool",
    "GuardrailFunctionOutput",
    "Handoff",
    "HandoffCallItem",
    "HandoffInputData",
    "HandoffOutputItem",
    "InMemorySession",
    "InputGuardrail",
    "InputGuardrailTripwireTriggered",
    "JSONLSession",
    "JSONLTraceProcessor",
    "MessageOutputItem",
    "OutputGuardrail",
    "OutputGuardrailTripwireTriggered",
    "PermissionDecision",
    "PermissionPolicy",
    "PromptApprove",
    "ReasoningItem",
    "RunConfig",
    "RunItem",
    "RunResult",
    "RunState",
    "RunStreamed",
    "Runner",
    "SchemaTool",
    "Session",
    "SessionSettings",
    "Span",
    "ToolApprovalItem",
    "ToolCallItem",
    "ToolCallOutputItem",
    "ToolGuardrailFunctionOutput",
    "ToolInputGuardrailTripwireTriggered",
    "ToolOutputGuardrailTripwireTriggered",
    "Trace",
    "TraceProcessor",
    "Usage",
    "_ToolApprovalItem",
    "add_trace_processor",
    "define_tool",
    "disable_tracing",
    "enable_tracing",
    "handoff",
    "input_guardrail",
    "output_guardrail",
    "span",
    "tool",
    "tool_input_guardrail",
    "tool_output_guardrail",
    "trace",
    # harness tools
    "BaseTool",
    # wire protocol
    "AssistantMessage",
    "ConstrainedSampling",
    "FileChanges",
    "ImageContent",
    "Message",
    "StopReason",
    "StreamDone",
    "StreamError",
    "StreamPart",
    "TextContent",
    "TextPart",
    "ThinkingContent",
    "ThinkPart",
    "ToolCall",
    "ToolCallDelta",
    "ToolCallEnd",
    "ToolCallStart",
    "ToolDefinition",
    "ToolParameter",
    "ToolResult",
    "ToolResultMessage",
    "UserMessage",
    "format_error",
    # plugin kernel
    "DEFAULT_PROFILE",
    "STANDARD_KEYS",
    "ConfigError",
    "Context",
    "CycleError",
    "DispatchMode",
    "DuplicateRowError",
    "EventHub",
    "EventModeError",
    "PatchError",
    "Plugin",
    "PluginError",
    "PluginHost",
    "Row",
    "RowError",
    "Service",
    "ServiceMissingError",
    "active_context",
    "active_host",
    "apply_patch",
    "apply_plugin",
    "as_plugin",
    "boot",
    "compose",
    "dump_config",
    "load_profile",
    "load_yaml",
    "load_yaml_file",
    "mount_tool",
    "plugin",
    "profile_dir",
    "profiles_dir",
    "reset",
    "resolve_plugin",
    "rows_from_layer",
    "shutdown",
    "validate_config",
    # extension api
    "ALL_EVENTS",
    "CommandOutcome",
    "ExtensionActions",
    "ExtensionAPI",
    "ExtensionCommand",
    "ExtensionError",
    "ExtensionFlag",
    "ExtensionNativeProviderRegistration",
    "ExtensionProviderRegistration",
    "ExtensionRecord",
    "ExtensionShortcut",
    "ExtensionTool",
    "ExtensionUIContext",
    "NO_OP_UI_CONTEXT",
    "blocking_result",
    "events",
    # live-composition helpers
    "api",
    "context",
    "emit",
    "emit_nowait",
    "on",
    "register_command",
    "register_provider",
    "register_tool",
    "unregister_provider",
    # lazy (ctx.ai / ctx.coding_agent)
    "ApiType",
    "BaseProvider",
    "LLMStream",
    "Model",
    "ProviderConfig",
    "ProviderInfo",
    "render_run",
    "resolve_prompt",
    "run_headless",
]

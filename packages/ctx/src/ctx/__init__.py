"""Ctx — the minimalist agent harness.

Re-exports the whole agent surface. Every name here is resolved lazily through
:func:`__getattr__`, because importing :mod:`ctx` used to import the entire SDK
at package-import time.

``ctx.coding_agent`` and ``ctx.tui`` ship in the ``ctx-code`` distribution, not
in ``ctx`` itself. Names they provide (``config``, ``run_headless``, ...) only
resolve when ``ctx-code`` is installed; every other name comes from the harness.

Keep ``_SOURCES`` in this order so a name exported by more than one of them
still resolves to the same object the eager re-exports produced.
"""

import importlib
from pkgutil import extend_path
from typing import TYPE_CHECKING, Any

# ``ctx-code`` ships ``ctx.coding_agent`` / ``ctx.tui`` from its own sources.
# Editable installs keep the two trees on separate ``sys.path`` entries, so the
# submodules only resolve if this package's path is extended to cover them.
__path__ = extend_path(__path__, __name__)

if TYPE_CHECKING:
    from ctx.agent.sdk import (
        Agent,
        AllowlistApprove,
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
        RunStreamed,
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
    from ctx.coding_agent import (  # ty: ignore[import-not-found]
        AVAILABLE_BINARIES,
        CONFIG_DIR_NAME,
        CURRENT_CONFIG_VERSION,
        PACKAGE_NAME,
        VERSION,
        _exit_code,
        add_recent_model,
        config,
        consume_config_warnings,
        format_version,
        get_agents_dir,
        get_config,
        get_config_dir,
        get_last_selected,
        get_recent_models,
        reload_config,
        render_run,
        reset_config,
        resolve_prompt,
        run_headless,
        set_colored_tool_badge,
        set_config,
        set_git_context,
        set_last_selected,
        set_model_provider_filter,
        set_notifications_enabled,
        set_permissions_mode,
        set_show_welcome_shortcuts,
        set_theme,
        set_thinking_lines,
        update_available_binaries,
    )
    from ctx.protocol import Message

_SOURCES = ("ctx.agent.sdk", "ctx.coding_agent", "ctx.protocol")


def __getattr__(name: str) -> Any:
    for module_name in _SOURCES:
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            # Optional product layer (``ctx.coding_agent`` ships in ctx-code).
            # Only skip when the source module itself is absent — a missing
            # transitive dep inside it must still surface.
            if exc.name != module_name:
                raise
            continue
        if (value := getattr(module, name, None)) is not None:
            # Cache into ``__dict__``: PEP 562 only fires ``__getattr__`` while
            # the name is absent, and the import system would otherwise rebind
            # any submodule sharing an exported name onto this package.
            globals()[name] = value
            return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(__all__)


__all__ = [
    "AVAILABLE_BINARIES",
    "CONFIG_DIR_NAME",
    "CURRENT_CONFIG_VERSION",
    "PACKAGE_NAME",
    "VERSION",
    # SDK
    "Agent",
    "AllowlistApprove",
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
    "Message",
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
    "RunStreamed",
    "Runner",
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
    "_exit_code",
    "add_recent_model",
    "add_trace_processor",
    "config",
    "consume_config_warnings",
    "disable_tracing",
    "enable_tracing",
    "format_version",
    "get_agents_dir",
    "get_config",
    "get_config_dir",
    "get_last_selected",
    "get_recent_models",
    "handoff",
    "input_guardrail",
    "output_guardrail",
    "reload_config",
    "render_run",
    "reset_config",
    "resolve_prompt",
    "run_headless",
    "set_colored_tool_badge",
    "set_config",
    "set_git_context",
    "set_last_selected",
    "set_model_provider_filter",
    "set_notifications_enabled",
    "set_permissions_mode",
    "set_show_welcome_shortcuts",
    "set_theme",
    "set_thinking_lines",
    "span",
    "tool",
    "tool_input_guardrail",
    "tool_output_guardrail",
    "trace",
    "update_available_binaries",
]

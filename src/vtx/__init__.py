"""Vtx — the minimalist coding agent harness.

Re-exports the whole agent surface. Every name here is resolved lazily through
:func:`__getattr__`, because importing :mod:`vtx` used to import the entire SDK
at package-import time: the console script ``vtx`` lives in
``vtx.coding_agent.cli``, so ``vtx --help`` dragged in the agent loop, the
plugin kernel, and every provider client before argparse ran.

Keep ``_SOURCES`` in this order so a name exported by more than one of them
still resolves to the same object the eager re-exports produced.
"""

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from vtx.agent.sdk import (
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
    from vtx.coding_agent import (
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
    from vtx.protocol import Message

_SOURCES = ("vtx.agent.sdk", "vtx.coding_agent", "vtx.protocol")


def __getattr__(name: str) -> Any:
    for module_name in _SOURCES:
        module = importlib.import_module(module_name)
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

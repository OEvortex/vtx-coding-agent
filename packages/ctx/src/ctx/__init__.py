"""Ctx — the minimalist agent harness.

Re-exports the whole agent surface. Every name here is resolved lazily through
:func:`__getattr__`, because importing :mod:`ctx` used to import the entire SDK
at package-import time.

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
    from ctx.protocol import Message

_SOURCES = ("ctx.agent.sdk", "ctx.protocol")


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
    "add_trace_processor",
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
]

"""
VTX Agentic SDK.

A programmatic, multi-agent interface to Vtx's runtime. Lets you build
agentic applications on top of Vtx's lean core, 18+ provider catalog, and
Pydantic-typed tool system.

Quick start::

    from vtx.sdk import Agent, Runner, tool

    @tool
    def get_weather(city: str) -> str:
        \"\"\"Look up the current weather for a city.\"\"\"
        return f\"Sunny in {city}\"

    agent = Agent(
        name=\"Weather bot\",
        instructions=\"Be concise.\",
        model=\"gpt-4o-mini\",
        tools=[get_weather],
    )

    result = Runner.run_sync(agent, \"Weather in Tokyo?\")
    print(result.final_output)
"""

from __future__ import annotations

from ._version import __version__
from .agent import Agent, AgentOutputSchema
from .approvals import ApprovalDecision, RunState, ToolApprovalItem
from .guardrails import (
    GuardrailFunctionOutput,
    InputGuardrail,
    InputGuardrailTripwireTriggered,
    OutputGuardrail,
    OutputGuardrailTripwireTriggered,
    ToolGuardrailFunctionOutput,
    ToolInputGuardrailTripwireTriggered,
    ToolOutputGuardrailTripwireTriggered,
    input_guardrail,
    output_guardrail,
    tool_input_guardrail,
    tool_output_guardrail,
)
from .handoffs import Handoff, HandoffInputData, handoff
from .items import (
    HandoffCallItem,
    HandoffOutputItem,
    MessageOutputItem,
    ReasoningItem,
    RunItem,
    ToolCallItem,
    ToolCallOutputItem,
)
from .items import ToolApprovalItem as _ToolApprovalItem
from .permissions import (
    AllowlistApprove,
    AutoApprove,
    PermissionDecision,
    PermissionPolicy,
    PromptApprove,
)
from .results import RunResult, Usage
from .run_config import RunConfig
from .runner import Runner, RunStreamed
from .sessions import InMemorySession, JSONLSession, Session, SessionSettings
from .tools import FunctionTool, tool
from .tracing import Span, Trace, add_trace_processor, disable_tracing, enable_tracing, span, trace
from .tracing.exporters import ConsoleTraceProcessor, JSONLTraceProcessor
from .tracing.processor import TraceProcessor

__all__ = [
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
    "__version__",
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

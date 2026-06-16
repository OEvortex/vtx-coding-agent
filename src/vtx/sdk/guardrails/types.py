"""Guardrail type definitions shared by input/output/tool guardrails."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ...core.types import TextContent, ToolResultMessage


@dataclass
class GuardrailFunctionOutput:
    """Result of an input or output guardrail check.

    Mirrors the shape of OpenAI's ``GuardrailFunctionOutput``. If
    ``tripwire_triggered`` is True, the run aborts with a tripwire
    exception.
    """

    output_info: Any = None
    tripwire_triggered: bool = False


@dataclass
class ToolGuardrailFunctionOutput:
    """Result of a tool-level guardrail check."""

    output_info: Any = None
    tripwire_triggered: bool = False

    @classmethod
    def allow(cls, output_info: Any = None) -> ToolGuardrailFunctionOutput:
        return cls(output_info=output_info, tripwire_triggered=False)

    @classmethod
    def reject_content(cls, message: str, output_info: Any = None) -> ToolGuardrailFunctionOutput:
        # The SDK runner inspects the ``message`` attribute to know what
        # string to feed back to the model when the tool is blocked.
        return cls(
            output_info={
                "rejected_message": message,
                **({"info": output_info} if output_info else {}),
            },
            tripwire_triggered=False,
        )

    @classmethod
    def raise_exception(cls, output_info: Any = None) -> ToolGuardrailFunctionOutput:
        return cls(output_info=output_info, tripwire_triggered=True)


@dataclass
class _InputGuardrailData:
    """Payload passed to input guardrail functions."""

    context: Any  # RunContextWrapper
    agent: Any  # SDK Agent
    input: str | list[Any]


@dataclass
class _OutputGuardrailData:
    """Payload passed to output guardrail functions."""

    context: Any
    agent: Any
    output: Any


@dataclass
class _ToolInputGuardrailData:
    context: Any
    tool_name: str
    tool_arguments: str | None


@dataclass
class _ToolOutputGuardrailData:
    context: Any
    tool_name: str
    tool_result: Any


@dataclass
class ToolGuardrailSpec:
    """Normalized description of a single tool-level guardrail."""

    name: str
    func: Callable[..., Any]
    is_async: bool


def normalize_tool_guardrail_specs(items: list[Any]) -> list[ToolGuardrailSpec]:
    """Convert user-supplied guardrail callables (or decorated specs) into
    ``ToolGuardrailSpec`` records.
    """
    specs: list[ToolGuardrailSpec] = []
    for item in items:
        if isinstance(item, ToolGuardrailSpec):
            specs.append(item)
        elif callable(item):
            specs.append(
                ToolGuardrailSpec(
                    name=getattr(item, "__name__", "tool_guardrail"), func=item, is_async=False
                )
            )
        else:
            raise TypeError(f"Unsupported tool guardrail: {item!r}")
    return specs


# Convenience: a tool result helper for guardrail replacement.
def _replace_tool_result_text(result: ToolResultMessage, new_text: str) -> ToolResultMessage:
    """Return a copy of ``result`` with its text content replaced by ``new_text``."""
    return ToolResultMessage(
        tool_call_id=result.tool_call_id,
        tool_name=result.tool_name,
        content=[TextContent(text=new_text)],
        ui_summary=result.ui_summary,
        ui_details=result.ui_details,
        ui_details_full=result.ui_details_full,
        is_error=result.is_error,
        file_changes=result.file_changes,
    )


_ = _InputGuardrailData
_ = _OutputGuardrailData
_ = _ToolInputGuardrailData
_ = _ToolOutputGuardrailData
_ = _replace_tool_result_text

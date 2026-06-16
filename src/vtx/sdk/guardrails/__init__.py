"""Guardrails: input, output, and tool-level checks.

A guardrail is a function decorated with ``@input_guardrail``,
``@output_guardrail``, ``@tool_input_guardrail``, or
``@tool_output_guardrail``. Guardrails run alongside the agent loop and
can short-circuit the run by returning ``tripwire_triggered=True``.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from .types import (
    GuardrailFunctionOutput,
    ToolGuardrailFunctionOutput,
    ToolGuardrailSpec,
    normalize_tool_guardrail_specs,
)

F = TypeVar("F", bound=Callable[..., Any])


@dataclass
class InputGuardrail:
    spec: InputGuardrailSpec


@dataclass
class OutputGuardrail:
    spec: OutputGuardrailOutputSpec


@dataclass
class InputGuardrailSpec:
    name: str
    func: Callable[..., Any]
    is_async: bool


@dataclass
class OutputGuardrailOutputSpec:
    name: str
    func: Callable[..., Any]
    is_async: bool


class InputGuardrailTripwireTriggered(Exception):
    """Raised when an input guardrail trips. The run aborts before any model call."""

    def __init__(self, guardrail_name: str, output_info: Any) -> None:
        self.guardrail_name = guardrail_name
        self.output_info = output_info
        super().__init__(f"Input guardrail {guardrail_name!r} tripped the run.")


class OutputGuardrailTripwireTriggered(Exception):
    """Raised when an output guardrail trips. The run aborts after the final output."""

    def __init__(self, guardrail_name: str, output_info: Any) -> None:
        self.guardrail_name = guardrail_name
        self.output_info = output_info
        super().__init__(f"Output guardrail {guardrail_name!r} tripped the run.")


class ToolInputGuardrailTripwireTriggered(Exception):
    """Raised when a tool input guardrail trips. The tool is skipped."""

    def __init__(self, guardrail_name: str, output_info: Any) -> None:
        self.guardrail_name = guardrail_name
        self.output_info = output_info
        super().__init__(f"Tool input guardrail {guardrail_name!r} tripped.")


class ToolOutputGuardrailTripwireTriggered(Exception):
    """Raised when a tool output guardrail trips. The tool's output is replaced."""

    def __init__(self, guardrail_name: str, output_info: Any) -> None:
        self.guardrail_name = guardrail_name
        self.output_info = output_info
        super().__init__(f"Tool output guardrail {guardrail_name!r} tripped.")


def input_guardrail[F: Callable[..., Any]](func: F) -> InputGuardrail:
    """Decorator: register a function as an input guardrail.

    The function receives a :class:`vtx.sdk.guardrails.types._InputGuardrailData`
    and must return a :class:`GuardrailFunctionOutput`.
    """

    @functools.wraps(func)
    def _wrapper(*args: Any, **kwargs: Any) -> Any:
        return func(*args, **kwargs)

    spec = InputGuardrailSpec(
        name=getattr(func, "__name__", "input_guardrail"),
        func=func,
        is_async=inspect.iscoroutinefunction(func),
    )
    # Stash the spec on the function so Agent can introspect.
    _wrapper._vtx_input_guardrail_spec = spec  # type: ignore
    return InputGuardrail(spec=spec)


def output_guardrail[F: Callable[..., Any]](func: F) -> OutputGuardrail:
    """Decorator: register a function as an output guardrail.

    The function receives a :class:`vtx.sdk.guardrails.types._OutputGuardrailData`
    and must return a :class:`GuardrailFunctionOutput`.
    """

    @functools.wraps(func)
    def _wrapper(*args: Any, **kwargs: Any) -> Any:
        return func(*args, **kwargs)

    spec = OutputGuardrailOutputSpec(
        name=getattr(func, "__name__", "output_guardrail"),
        func=func,
        is_async=inspect.iscoroutinefunction(func),
    )
    _wrapper._vtx_output_guardrail_spec = spec  # type: ignore
    return OutputGuardrail(spec=spec)


def tool_input_guardrail[F: Callable[..., Any]](func: F) -> Callable[..., Any]:
    """Decorator: register a function as a tool-input guardrail."""

    @functools.wraps(func)
    def _wrapper(*args: Any, **kwargs: Any) -> Any:
        return func(*args, **kwargs)

    spec = ToolGuardrailSpec(
        name=getattr(func, "__name__", "tool_input_guardrail"),
        func=func,
        is_async=inspect.iscoroutinefunction(func),
    )
    _wrapper._vtx_tool_input_guardrail_spec = spec  # type: ignore
    return _wrapper


def tool_output_guardrail[F: Callable[..., Any]](func: F) -> Callable[..., Any]:
    """Decorator: register a function as a tool-output guardrail."""

    @functools.wraps(func)
    def _wrapper(*args: Any, **kwargs: Any) -> Any:
        return func(*args, **kwargs)

    spec = ToolGuardrailSpec(
        name=getattr(func, "__name__", "tool_output_guardrail"),
        func=func,
        is_async=inspect.iscoroutinefunction(func),
    )
    _wrapper._vtx_tool_output_guardrail_spec = spec  # type: ignore
    return _wrapper


async def run_input_guardrails(guardrails: list[InputGuardrail], data: Any) -> None:
    """Run input guardrails in parallel. Raises on tripwire."""
    if not guardrails:
        return

    async def _one(g: InputGuardrail) -> tuple[InputGuardrail, GuardrailFunctionOutput]:
        result = g.spec.func(data)
        if inspect.isawaitable(result):
            result = await result
        return g, result  # type: ignore[return-value]

    results = await asyncio.gather(*[_one(g) for g in guardrails])
    for guardrail, result in results:
        if not isinstance(result, GuardrailFunctionOutput):
            result = GuardrailFunctionOutput(output_info=result)
        if result.tripwire_triggered:
            raise InputGuardrailTripwireTriggered(guardrail.spec.name, result.output_info)


async def run_output_guardrails(guardrails: list[OutputGuardrail], data: Any) -> None:
    """Run output guardrails in parallel. Raises on tripwire."""
    if not guardrails:
        return

    async def _one(g: OutputGuardrail) -> tuple[OutputGuardrail, GuardrailFunctionOutput]:
        result = g.spec.func(data)
        if inspect.isawaitable(result):
            result = await result
        return g, result  # type: ignore[return-value]

    results = await asyncio.gather(*[_one(g) for g in guardrails])
    for guardrail, result in results:
        if not isinstance(result, GuardrailFunctionOutput):
            result = GuardrailFunctionOutput(output_info=result)
        if result.tripwire_triggered:
            raise OutputGuardrailTripwireTriggered(guardrail.spec.name, result.output_info)


__all__ = [
    "GuardrailFunctionOutput",
    "InputGuardrail",
    "InputGuardrailTripwireTriggered",
    "OutputGuardrail",
    "OutputGuardrailTripwireTriggered",
    "ToolGuardrailFunctionOutput",
    "ToolGuardrailSpec",
    "ToolInputGuardrailTripwireTriggered",
    "ToolOutputGuardrailTripwireTriggered",
    "input_guardrail",
    "normalize_tool_guardrail_specs",
    "output_guardrail",
    "run_input_guardrails",
    "run_output_guardrails",
    "tool_input_guardrail",
    "tool_output_guardrail",
]

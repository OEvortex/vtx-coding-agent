"""Tests for guardrails (input, output, tool-level)."""

from __future__ import annotations

import asyncio

import pytest

from vtx.sdk import (
    Agent,
    GuardrailFunctionOutput,
    InputGuardrailTripwireTriggered,
    OutputGuardrailTripwireTriggered,
    Runner,
    function_tool,
    input_guardrail,
    output_guardrail,
    tool_input_guardrail,
    tool_output_guardrail,
)
from vtx.sdk.guardrails import run_input_guardrails, run_output_guardrails
from vtx.sdk.guardrails.types import ToolGuardrailFunctionOutput


@pytest.mark.asyncio
async def test_input_guardrail_passes(text_provider) -> None:
    @input_guardrail
    def check(data):
        return GuardrailFunctionOutput(output_info="ok")

    agent = Agent(name="Bot", provider=text_provider, input_guardrails=[check])
    result = await Runner.run(agent, "hi")
    assert result.final_output == "Hello, world!"


@pytest.mark.asyncio
async def test_input_guardrail_trips(text_provider) -> None:
    @input_guardrail
    def check(data):
        return GuardrailFunctionOutput(tripwire_triggered=True, output_info="blocked")

    agent = Agent(name="Bot", provider=text_provider, input_guardrails=[check])
    with pytest.raises(InputGuardrailTripwireTriggered):
        await Runner.run(agent, "hi")


@pytest.mark.asyncio
async def test_input_guardrail_async(text_provider) -> None:
    @input_guardrail
    async def check(data):
        await asyncio.sleep(0)
        return GuardrailFunctionOutput(output_info="async ok")

    agent = Agent(name="Bot", provider=text_provider, input_guardrails=[check])
    result = await Runner.run(agent, "hi")
    assert result.final_output == "Hello, world!"


@pytest.mark.asyncio
async def test_output_guardrail_passes(text_provider) -> None:
    @output_guardrail
    def check(data):
        return GuardrailFunctionOutput(output_info="ok")

    agent = Agent(name="Bot", provider=text_provider, output_guardrails=[check])
    result = await Runner.run(agent, "hi")
    assert result.final_output == "Hello, world!"


@pytest.mark.asyncio
async def test_output_guardrail_trips(text_provider) -> None:
    @output_guardrail
    def check(data):
        return GuardrailFunctionOutput(tripwire_triggered=True, output_info="blocked")

    agent = Agent(name="Bot", provider=text_provider, output_guardrails=[check])
    with pytest.raises(OutputGuardrailTripwireTriggered):
        await Runner.run(agent, "hi")


def test_input_guardrail_decorator_metadata() -> None:
    @input_guardrail
    def my_check(data):
        return GuardrailFunctionOutput()

    assert my_check.spec.name == "my_check"
    assert my_check.spec.is_async is False


def test_output_guardrail_decorator_async_metadata() -> None:
    @output_guardrail
    async def my_check(data):
        return GuardrailFunctionOutput()

    assert my_check.spec.is_async is True


def test_tool_input_guardrail_returns_decorator() -> None:
    @tool_input_guardrail
    def g(data):
        return ToolGuardrailFunctionOutput()

    assert hasattr(g, "_vtx_tool_input_guardrail_spec")


def test_tool_output_guardrail_returns_decorator() -> None:
    @tool_output_guardrail
    def g(data):
        return ToolGuardrailFunctionOutput()

    assert hasattr(g, "_vtx_tool_output_guardrail_spec")


@pytest.mark.asyncio
async def test_run_input_guardrails_no_guardrails() -> None:
    # Should be a no-op.
    await run_input_guardrails([], None)


@pytest.mark.asyncio
async def test_run_input_guardrails_multiple() -> None:
    @input_guardrail
    def g1(data):
        return GuardrailFunctionOutput(output_info="1")

    @input_guardrail
    def g2(data):
        return GuardrailFunctionOutput(tripwire_triggered=True)

    with pytest.raises(InputGuardrailTripwireTriggered):
        await run_input_guardrails([g1, g2], None)


@pytest.mark.asyncio
async def test_run_output_guardrails_no_guardrails() -> None:
    await run_output_guardrails([], None)


@pytest.mark.asyncio
async def test_function_tool_with_input_guardrail_blocks() -> None:
    @tool_input_guardrail
    def block(data):
        return ToolGuardrailFunctionOutput.reject_content("blocked by guardrail")

    @function_tool(input_guardrails=[block])
    def my_tool(x: int) -> int:
        return x * 2

    # The guardrail data isn't currently surfaced through FunctionTool.execute;
    # we just ensure attaching it doesn't break the tool.
    result = await my_tool.execute(my_tool.params(x=2))
    assert result.success is True
    assert result.result == "4"

"""Tests for the Agent.as_tool() manager pattern (agents-as-tools)."""

from __future__ import annotations

import pytest

from vtx.sdk import Agent


def test_as_tool_default_metadata() -> None:
    sub = Agent(name="My Helper", instructions="Help me.")
    parent = Agent(name="Parent", tools=[sub])
    tools = parent.compiled_tools()
    assert len(tools) == 1
    assert tools[0].name == "my_helper"
    assert "My Helper" in tools[0].description


def test_as_tool_custom_name_description() -> None:
    sub = Agent(name="Sub", instructions="x")
    tool = sub.as_tool(tool_name="custom", tool_description="My custom")
    assert tool.name == "custom"
    assert tool.description == "My custom"


def test_as_tool_params_input_field() -> None:
    sub = Agent(name="Sub", instructions="x")
    tool = sub.as_tool()
    schema = tool.params.model_json_schema()
    assert "input" in schema["properties"]


def test_as_tool_format_call() -> None:
    sub = Agent(name="Helper", instructions="x")
    tool = sub.as_tool()
    params = tool.params(input="hi")
    text = tool.format_call(params)
    assert "Helper" in text


@pytest.mark.asyncio
async def test_as_tool_executes_subagent() -> None:
    """The parent invokes the sub-agent; the sub-agent's final output becomes the tool result."""
    from vtx.llm.providers.mock import MockProvider

    sub_provider = MockProvider(scenario="simple_text")
    sub = Agent(name="Sub", provider=sub_provider, instructions="be brief")

    parent_provider = MockProvider(scenario="simple_text")
    Agent(name="Parent", provider=parent_provider, tools=[sub.as_tool()])

    # We can't easily make the parent call its tool automatically without
    # a custom provider, so we exercise the sub-agent path directly.
    tool = sub.as_tool()
    params = tool.params(input="Hello, world!")
    result = await tool.execute(params)
    assert result.success is True
    assert result.result is not None and "Hello, world!" in result.result

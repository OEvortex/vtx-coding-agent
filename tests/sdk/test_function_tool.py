"""Tests for ``@tool`` and the ``FunctionTool`` class."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel

from vtx.core.types import ToolResult
from vtx.sdk import tool
from vtx.sdk.tools import FunctionTool, _format_call_from_dict


def test_tool_decorator_basic() -> None:
    @tool
    def get_weather(city: str) -> str:
        """Return weather for a city."""
        return f"Sunny in {city}"

    assert isinstance(get_weather, FunctionTool)
    assert get_weather.name == "get_weather"
    assert "weather" in get_weather.description.lower()
    assert get_weather.mutating is True


def test_tool_with_overrides() -> None:
    @tool(name="renamed", description="Custom", mutating=False, tool_icon="*")
    def f(x: int) -> int:
        return x * 2

    assert f.name == "renamed"
    assert f.description == "Custom"
    assert f.mutating is False
    assert f.tool_icon == "*"


def test_tool_pydantic_params_generation() -> None:
    @tool
    def search(query: str, limit: int = 10) -> str:
        """Search something."""
        return f"{query} {limit}"

    params_model = search.params
    schema = params_model.model_json_schema()
    assert "query" in schema["properties"]
    assert "limit" in schema["properties"]
    assert "query" in schema.get("required", [])
    assert "limit" not in schema.get("required", [])


def test_tool_optional_param() -> None:
    @tool
    def f(x: str | None = None) -> str:
        return x or "default"

    params = f.params(x=None)
    assert params.model_dump(exclude_none=True) == {}


@pytest.mark.asyncio
async def test_tool_execute_sync() -> None:
    @tool
    def add(a: int, b: int) -> int:
        return a + b

    params = add.params(a=2, b=3)
    result = await add.execute(params)
    assert result.success is True
    assert result.result == "5"


@pytest.mark.asyncio
async def test_tool_execute_async() -> None:
    @tool
    async def slow_add(a: int, b: int) -> int:
        await asyncio.sleep(0.001)
        return a + b

    params = slow_add.params(a=10, b=20)
    result = await slow_add.execute(params)
    assert result.success is True
    assert result.result == "30"


@pytest.mark.asyncio
async def test_tool_execute_exception() -> None:
    @tool
    def boom(x: int) -> int:
        raise ValueError("nope")

    params = boom.params(x=1)
    result = await boom.execute(params)
    assert result.success is False
    assert "nope" in result.result


@pytest.mark.asyncio
async def test_tool_execute_tool_result_passthrough() -> None:
    @tool
    def make_result() -> ToolResult:
        return ToolResult(success=True, result="hi", ui_summary="hello")

    params = make_result.params()
    result = await make_result.execute(params)
    assert result.success is True
    assert result.result == "hi"
    assert result.ui_summary == "hello"


def test_tool_format_call() -> None:
    @tool
    def f(x: int, y: str = "default") -> str:
        return ""

    params = f.params(x=42, y="hello")
    text = f.format_call(params)
    assert "x=42" in text
    assert "y=hello" in text


def test_tool_no_args() -> None:
    @tool
    def noop() -> str:
        """A no-op tool."""
        return "ok"

    params = noop.params()
    result = asyncio.run(noop.execute(params))
    assert result.result == "ok"


def test_tool_docstring_arg_descriptions() -> None:
    @tool
    def search(query: str, limit: int = 10) -> str:
        """Search the index.

        Args:
            query: The search query string.
            limit: Maximum number of results to return.
        """
        return ""

    schema = search.params.model_json_schema()
    assert (
        "search the index" in search.description.lower() or "search" in search.description.lower()
    )
    assert "query" in schema["properties"]["query"].get("description", "").lower()
    assert "maximum" in schema["properties"]["limit"]["description"].lower()


def test_tool_needs_approval() -> None:
    @tool(needs_approval=True)
    def dangerous() -> str:
        return "rm -rf /"

    assert dangerous.needs_approval is True


def test_tool_pydantic_model_arg() -> None:
    class Nested(BaseModel):
        x: int
        y: str

    @tool
    def consume(n: Nested) -> str:
        return f"{n.x} {n.y}"

    params = consume.params(n={"x": 1, "y": "hi"})
    result = asyncio.run(consume.execute(params))
    assert result.result == "1 hi"


def test_format_call_from_dict_empty() -> None:
    assert _format_call_from_dict({}) == ""

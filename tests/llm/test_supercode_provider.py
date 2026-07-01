"""Tests for the Supercode provider.

Tests the provider's message conversion, tool conversion (both ToolDefinition
and dict inputs), and multi-turn tool calling flow.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from vtx.core.types import (
    AssistantMessage,
    StopReason,
    StreamDone,
    TextContent,
    TextPart,
    ToolCall,
    ToolCallDelta,
    ToolCallStart,
    ToolDefinition,
    ToolResultMessage,
    UserMessage,
)
from vtx.llm.base import ProviderConfig
from vtx.llm.providers.supercode import SupercodeProvider


def _make_tool_definition(name: str, desc: str = "") -> ToolDefinition:
    return ToolDefinition(name=name, description=desc, parameters={"type": "object"})


def _make_tool_dict(name: str, desc: str = "") -> dict[str, Any]:
    return {
        "type": "function",
        "function": {"name": name, "description": desc, "parameters": {"type": "object"}},
    }


async def _collect(stream: Any) -> list[Any]:
    parts: list[Any] = []
    async for part in stream:
        parts.append(part)
    return parts


# ── _convert_tools ────────────────────────────────────────────────────────────


def test_convert_tools_with_tooldefinition_objects():
    """_convert_tools handles ToolDefinition objects correctly."""
    provider = SupercodeProvider(ProviderConfig(api_key="test"))
    tools = [_make_tool_definition("get_weather", "Get weather")]
    result = provider._convert_tools(tools)
    assert len(result) == 1
    assert result[0]["type"] == "function"
    assert result[0]["function"]["name"] == "get_weather"
    assert result[0]["function"]["description"] == "Get weather"


def test_convert_tools_with_dicts():
    """_convert_tools handles dict inputs (from provisioning flow)."""
    provider = SupercodeProvider(ProviderConfig(api_key="test"))
    tools = [_make_tool_dict("get_weather", "Get weather")]
    result = provider._convert_tools(tools)
    assert len(result) == 1
    assert result[0]["type"] == "function"
    assert result[0]["function"]["name"] == "get_weather"
    assert result[0]["function"]["description"] == "Get weather"


def test_convert_tools_with_mixed_inputs():
    """_convert_tools handles a mix of ToolDefinition objects and dicts."""
    provider = SupercodeProvider(ProviderConfig(api_key="test"))
    tools = [_make_tool_definition("tool_a", "A"), _make_tool_dict("tool_b", "B")]
    result = provider._convert_tools(tools)
    assert len(result) == 2
    assert result[0]["function"]["name"] == "tool_a"
    assert result[1]["function"]["name"] == "tool_b"


def test_convert_tools_empty_list():
    """_convert_tools returns empty list for empty input."""
    provider = SupercodeProvider(ProviderConfig(api_key="test"))
    result = provider._convert_tools([])
    assert result == []


# ── _convert_messages ────────────────────────────────────────────────────────


def test_convert_user_message():
    provider = SupercodeProvider(ProviderConfig(api_key="test"))
    msgs = [UserMessage(content="hello")]
    result = provider._convert_messages(msgs, None)
    assert len(result) == 1
    assert result[0].role == "user"
    assert result[0].content == "hello"


def test_convert_assistant_message_with_tool_calls():
    provider = SupercodeProvider(ProviderConfig(api_key="test"))
    msgs = [
        AssistantMessage(
            content=[
                TextContent(text="Let me check"),
                ToolCall(id="call-1", name="get_weather", arguments={"city": "NYC"}),
            ]
        )
    ]
    result = provider._convert_messages(msgs, None)
    assert len(result) == 1
    assert result[0].role == "assistant"
    assert "Let me check" in result[0].content
    assert result[0].metadata is not None
    assert "tool_calls" in result[0].metadata
    assert result[0].metadata["tool_calls"][0]["function"]["name"] == "get_weather"


def test_convert_tool_result_message():
    provider = SupercodeProvider(ProviderConfig(api_key="test"))
    msgs = [
        ToolResultMessage(
            tool_call_id="call-1",
            tool_name="get_weather",
            content=[TextContent(text="72°F, sunny")],
        )
    ]
    result = provider._convert_messages(msgs, None)
    assert len(result) == 1
    assert result[0].role == "tool"
    assert "72°F" in result[0].content
    assert result[0].metadata["tool_call_id"] == "call-1"


def test_convert_system_prompt():
    provider = SupercodeProvider(ProviderConfig(api_key="test"))
    msgs = [UserMessage(content="hi")]
    result = provider._convert_messages(msgs, system_prompt="You are a bot")
    assert len(result) == 2
    assert result[0].role == "system"
    assert result[0].content == "You are a bot"
    assert result[1].role == "user"


# ── Multi-turn tool calling simulation ───────────────────────────────────────
# These tests mock the SDK to simulate the full multi-turn flow without a
# real network connection.


def _make_mock_stream(chunks: list[dict[str, Any]]):
    """Create an async generator from a list of NDJSON-like chunks."""

    async def _gen():
        for c in chunks:
            yield c

    return _gen()


@pytest.mark.asyncio
async def test_single_turn_text_response():
    """Provider yields text parts and StreamDone from a text-only response."""
    provider = SupercodeProvider(ProviderConfig(api_key="test"))
    mock_chunks = [
        {"type": "text", "content": "Hello"},
        {"type": "text", "content": " world"},
        {"type": "usage", "usage": {"prompt_tokens": 5, "completion_tokens": 5}},
        {"type": "finish_reason", "finish_reason": "stop", "usage": {}},
    ]

    with patch.object(provider._sdk, "_stream_chat", return_value=_make_mock_stream(mock_chunks)):
        stream = await provider.stream([UserMessage(content="hi")])
        parts = await _collect(stream)

    text_parts = [p for p in parts if isinstance(p, TextPart)]
    assert len(text_parts) == 2
    assert text_parts[0].text == "Hello"
    assert text_parts[1].text == " world"

    done_parts = [p for p in parts if isinstance(p, StreamDone)]
    assert len(done_parts) == 1
    assert done_parts[0].stop_reason == StopReason.STOP


@pytest.mark.asyncio
async def test_single_turn_tool_calling():
    """Provider yields ToolCallStart/ToolCallDelta from a tool-calling response."""
    provider = SupercodeProvider(ProviderConfig(api_key="test"))

    tool_defs = [_make_tool_definition("get_weather", "Get the weather for a city")]
    mock_chunks = [
        {"type": "text", "content": "Let me check"},
        {
            "type": "tool_calls",
            "tool_calls": [{"id": "call-1", "name": "get_weather", "arguments": '{"city":"NYC"}'}],
        },
        {"type": "usage", "usage": {"prompt_tokens": 10, "completion_tokens": 5}},
        {"type": "finish_reason", "finish_reason": "tool_calls", "usage": {}},
    ]

    with patch.object(provider._sdk, "_stream_chat", return_value=_make_mock_stream(mock_chunks)):
        stream = await provider.stream(
            [UserMessage(content="What's the weather in NYC?")], tools=tool_defs
        )
        parts = await _collect(stream)

    text_parts = [p for p in parts if isinstance(p, TextPart)]
    assert len(text_parts) == 1
    assert text_parts[0].text == "Let me check"

    starts = [p for p in parts if isinstance(p, ToolCallStart)]
    assert len(starts) == 1
    assert starts[0].name == "get_weather"
    assert starts[0].id == "call-1"

    deltas = [p for p in parts if isinstance(p, ToolCallDelta)]
    assert len(deltas) == 1

    done = next(p for p in parts if isinstance(p, StreamDone))
    assert done.stop_reason == StopReason.TOOL_USE


@pytest.mark.asyncio
async def test_multi_turn_tool_calling():
    """Full multi-turn flow: tool call → tool result → final text response."""
    provider = SupercodeProvider(ProviderConfig(api_key="test"))
    tool_defs = [_make_tool_definition("get_weather", "Get weather")]

    # Turn 1: tool-calling response
    turn1_chunks = [
        {"type": "text", "content": "Let me look that up"},
        {
            "type": "tool_calls",
            "tool_calls": [{"id": "call-1", "name": "get_weather", "arguments": '{"city":"NYC"}'}],
        },
        {"type": "usage", "usage": {"prompt_tokens": 10, "completion_tokens": 5}},
        {"type": "finish_reason", "finish_reason": "tool_calls", "usage": {}},
    ]

    with patch.object(provider._sdk, "_stream_chat", return_value=_make_mock_stream(turn1_chunks)):
        stream = await provider.stream(
            [UserMessage(content="What's the weather in NYC?")], tools=tool_defs
        )
        parts1 = await _collect(stream)

    starts = [p for p in parts1 if isinstance(p, ToolCallStart)]
    assert len(starts) == 1
    assert starts[0].name == "get_weather"
    assert starts[0].id == "call-1"

    done = next(p for p in parts1 if isinstance(p, StreamDone))
    assert done.stop_reason == StopReason.TOOL_USE

    # Build messages for turn 2: include original user message, assistant
    # response with tool call, and tool result.
    turn2_messages = [
        UserMessage(content="What's the weather in NYC?"),
        AssistantMessage(
            content=[
                TextContent(text="Let me look that up"),
                ToolCall(id="call-1", name="get_weather", arguments={"city": "NYC"}),
            ]
        ),
        ToolResultMessage(
            tool_call_id="call-1",
            tool_name="get_weather",
            content=[TextContent(text="72°F, sunny")],
        ),
    ]

    # Turn 2: text response using the tool result
    turn2_chunks = [
        {"type": "text", "content": "The weather in NYC is 72°F and sunny."},
        {"type": "usage", "usage": {"prompt_tokens": 15, "completion_tokens": 8}},
        {"type": "finish_reason", "finish_reason": "stop", "usage": {}},
    ]

    with patch.object(provider._sdk, "_stream_chat", return_value=_make_mock_stream(turn2_chunks)):
        stream = await provider.stream(turn2_messages, tools=tool_defs)
        parts2 = await _collect(stream)

    text_parts = [p for p in parts2 if isinstance(p, TextPart)]
    assert len(text_parts) == 1
    assert "72°F" in text_parts[0].text

    done2 = next(p for p in parts2 if isinstance(p, StreamDone))
    assert done2.stop_reason == StopReason.STOP


# ── _map_finish_reason ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("stop", StopReason.STOP),
        ("null", StopReason.STOP),
        ("", StopReason.STOP),
        ("length", StopReason.LENGTH),
        ("max_tokens", StopReason.LENGTH),
        ("tool_calls", StopReason.TOOL_USE),
        ("unknown", StopReason.STOP),
    ],
)
def test_map_finish_reason(reason: str, expected: StopReason):
    assert SupercodeProvider._map_finish_reason(reason) == expected


# ── should_retry_for_error ─────────────────────────────────────────────────


def test_should_retry_for_connection_errors():
    provider = SupercodeProvider(ProviderConfig(api_key="test"))
    assert provider.should_retry_for_error(ConnectionError("connection refused")) is True
    assert provider.should_retry_for_error(RuntimeError("timeout occurred")) is True
    assert provider.should_retry_for_error(RuntimeError("connection reset")) is True
    assert provider.should_retry_for_error(RuntimeError("500 internal error")) is True
    assert provider.should_retry_for_error(RuntimeError("502 bad gateway")) is True
    assert provider.should_retry_for_error(RuntimeError("503 service unavailable")) is True


def test_should_not_retry_for_rate_limit():
    provider = SupercodeProvider(ProviderConfig(api_key="test"))
    assert provider.should_retry_for_error(RuntimeError("rate limit")) is False


def test_should_retry_for_unrelated_errors():
    provider = SupercodeProvider(ProviderConfig(api_key="test"))
    assert provider.should_retry_for_error(ValueError("bad request")) is False
    assert provider.should_retry_for_error(RuntimeError("invalid api key")) is False

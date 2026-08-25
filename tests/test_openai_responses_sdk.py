"""Tests for the OpenAI Responses API adapter (pi openai-responses parity)."""

from __future__ import annotations

import json

from vtx.ai.sdk.base import GenerationConfig, Message
from vtx.ai.sdk.openai_responses import OpenAIResponsesSDK


def _sdk() -> OpenAIResponsesSDK:
    return OpenAIResponsesSDK(api_key="test", provider_slug="openai-responses")


def _cfg(**kw) -> GenerationConfig:
    return GenerationConfig(model="gpt-5", **kw)


# ---------------------------------------------------------------------
# Payload lowering (pi's fromRequest)
# ---------------------------------------------------------------------


def test_system_and_user_lower_to_input_items():
    sdk = _sdk()
    messages = [Message(role="system", content="be brief"), Message(role="user", content="hello")]
    payload = sdk._build_payload(messages, _cfg())
    assert payload["input"] == [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "hello"},
    ]
    assert payload["store"] is False
    assert payload["stream"] is True


def test_reasoning_effort_uses_verified_map():
    sdk = _sdk()
    messages = [Message(role="user", content="hi")]
    payload = sdk._build_payload(
        messages,
        _cfg(
            thinking_level="high",
            thinking_level_map={"off": "none", "low": "low", "high": "high", "xhigh": None},
        ),
    )
    assert payload["reasoning"] == {"effort": "high"}


def test_reasoning_omitted_when_level_unsupported():
    sdk = _sdk()
    messages = [Message(role="user", content="hi")]
    payload = sdk._build_payload(
        messages,
        _cfg(
            thinking_level="xhigh", thinking_level_map={"off": "none", "low": "low", "xhigh": None}
        ),
    )
    # xhigh clamps down to high (nearest supported), which IS advertised.
    assert payload["reasoning"] == {"effort": "high"}


def test_reasoning_omitted_for_none_level():
    sdk = _sdk()
    payload = sdk._build_payload([Message(role="user", content="hi")], _cfg(thinking_level="none"))
    assert "reasoning" not in payload


def test_tools_flatten_to_function_shape():
    sdk = _sdk()
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "d",
                "parameters": {"type": "object"},
            },
        }
    ]
    payload = sdk._build_payload([Message(role="user", content="w")], _cfg(), tools=tools)
    assert payload["tools"][0]["type"] == "function"
    assert payload["tools"][0]["name"] == "get_weather"


def test_assistant_tool_calls_lower_to_function_call_items():
    sdk = _sdk()
    messages = [
        Message(role="user", content="weather?"),
        Message(
            role="assistant",
            content="",
            metadata={
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "get_weather", "arguments": '{"city":"sf"}'},
                    }
                ]
            },
        ),
        Message(role="tool", content="sunny", metadata={"tool_call_id": "call_1"}),
    ]
    payload = sdk._build_payload(messages, _cfg())
    kinds = [i.get("type") for i in payload["input"]]
    assert kinds == [None, "function_call", "function_call_output"]
    call_item = payload["input"][1]
    assert call_item["call_id"] == "call_1"
    assert call_item["name"] == "get_weather"
    out_item = payload["input"][2]
    assert out_item["call_id"] == "call_1"
    assert out_item["output"] == "sunny"


# ---------------------------------------------------------------------
# Stream state machine (pi's step dispatch)
# ---------------------------------------------------------------------


def _feed(sdk, events):
    state = sdk._new_stream_state()
    chunks = []
    for e in events:
        chunks.extend(sdk._stream_step(state, e))
    return state, chunks


def test_stream_text_and_reasoning_deltas():
    sdk = _sdk()
    _state, chunks = _feed(
        sdk,
        [
            {"type": "response.output_text.delta", "delta": "hello"},
            {"type": "response.reasoning_summary_text.delta", "delta": "thinking…"},
        ],
    )
    assert chunks == [
        {"type": "text", "content": "hello"},
        {"type": "reasoning", "content": "thinking…"},
    ]


def test_stream_function_call_lifecycle_emits_tool_call():
    sdk = _sdk()
    state, chunks = _feed(
        sdk,
        [
            {
                "type": "response.output_item.added",
                "output_index": 0,
                "item": {
                    "type": "function_call",
                    "id": "fc_1",
                    "call_id": "call_1",
                    "name": "get_weather",
                },
            },
            {"type": "response.function_call_arguments.delta", "item_id": "fc_1", "delta": '{"ci'},
            {
                "type": "response.function_call_arguments.done",
                "item_id": "fc_1",
                "arguments": '{"city": "sf"}',
            },
            {
                "type": "response.output_item.done",
                "output_index": 0,
                "item": {
                    "type": "function_call",
                    "id": "fc_1",
                    "call_id": "call_1",
                    "name": "get_weather",
                    "arguments": '{"city": "sf"}',
                },
            },
        ],
    )
    tool_chunks = [c for c in chunks if c["type"] == "tool_calls"]
    assert len(tool_chunks) == 1
    tc = tool_chunks[0]["tool_calls"][0]
    assert tc["id"] == "call_1"
    assert tc["name"] == "get_weather"
    assert json.loads(tc["arguments"]) == {"city": "sf"}
    assert state["has_function_call"] is True


def test_stream_completed_sets_usage_and_finish_reason():
    sdk = _sdk()
    _state, chunks = _feed(
        sdk,
        [
            {"type": "response.output_text.delta", "delta": "ok"},
            {
                "type": "response.completed",
                "response": {
                    "status": "completed",
                    "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                },
            },
        ],
    )
    usage = next(c for c in chunks if c["type"] == "usage")
    assert usage["usage"]["total_tokens"] == 15

    finish = [c for c in chunks if c["type"] == "finish_reason"]
    assert finish == [{"type": "finish_reason", "finish_reason": "stop"}]

import os
from unittest.mock import patch

import httpx
import pytest

from vtx.core.types import AssistantMessage, StopReason, TextContent, UserMessage
from vtx.llm.base import ProviderConfig
from vtx.llm.providers.anthropic_sdk import AnthropicSDKProvider


@pytest.fixture
def anthropic_provider():
    env = {"ANTHROPIC_API_KEY": "test-key"}
    with patch.dict(os.environ, env, clear=False):
        return AnthropicSDKProvider(ProviderConfig(model="claude-3-5-sonnet-latest"))


def test_anthropic_sdk_provider_name():
    env = {"ANTHROPIC_API_KEY": "test-key"}
    with patch.dict(os.environ, env, clear=False):
        provider = AnthropicSDKProvider(ProviderConfig(model="test"))
        assert provider.name == "anthropic"


def test_anthropic_sdk_provider_requires_api_key():
    with (
        patch.dict(os.environ, {}, clear=True),
        pytest.raises(ValueError, match="No API key found"),
    ):
        AnthropicSDKProvider(ProviderConfig(model="test"))


def test_convert_user_message_text(anthropic_provider):
    msg = UserMessage(content="hello world")
    sdk_msg = anthropic_provider._convert_user_message(msg)
    assert sdk_msg.role == "user"
    assert sdk_msg.content == "hello world"


def test_convert_assistant_message(anthropic_provider):
    msg = AssistantMessage(content=[TextContent(text="response text")])
    sdk_msg = anthropic_provider._convert_assistant_message(msg)
    assert sdk_msg.role == "assistant"
    assert sdk_msg.content == "response text"


def test_convert_tool_result(anthropic_provider):
    from vtx.core.types import ToolResultMessage

    msg = ToolResultMessage(
        tool_call_id="tc-1", tool_name="bash", content=[TextContent(text="output")]
    )
    sdk_msg = anthropic_provider._convert_tool_result(msg)
    assert sdk_msg.role == "tool"
    assert sdk_msg.content == "output"
    assert sdk_msg.metadata["tool_call_id"] == "tc-1"


def test_convert_tools(anthropic_provider):
    from vtx.core.types import ToolDefinition

    tools = [
        ToolDefinition(
            name="bash",
            description="Run a bash command",
            parameters={"type": "object", "properties": {"command": {"type": "string"}}},
        )
    ]
    converted = anthropic_provider._convert_tools(tools)
    assert len(converted) == 1
    assert converted[0]["type"] == "function"
    assert converted[0]["function"]["name"] == "bash"


def _make_mock_response(status_code: int) -> httpx.Response:
    return httpx.Response(status_code, request=httpx.Request("GET", "https://example.com"))


def test_should_retry_for_rate_limit(anthropic_provider):
    from anthropic import RateLimitError

    error = RateLimitError(message="rate limited", response=_make_mock_response(429), body=None)
    assert anthropic_provider.should_retry_for_error(error) is True


def test_should_retry_for_server_error(anthropic_provider):
    from anthropic import APIStatusError

    error = APIStatusError(message="server error", response=_make_mock_response(500), body=None)
    assert anthropic_provider.should_retry_for_error(error) is True


def test_should_not_retry_for_client_error(anthropic_provider):
    from anthropic import APIStatusError

    error = APIStatusError(message="bad request", response=_make_mock_response(400), body=None)
    assert anthropic_provider.should_retry_for_error(error) is False


def test_map_stop_reason(anthropic_provider):
    assert anthropic_provider._map_stop_reason("end_turn") == StopReason.STOP
    assert anthropic_provider._map_stop_reason("max_tokens") == StopReason.LENGTH
    assert anthropic_provider._map_stop_reason("tool_use") == StopReason.TOOL_USE
    assert anthropic_provider._map_stop_reason("unknown") == StopReason.STOP

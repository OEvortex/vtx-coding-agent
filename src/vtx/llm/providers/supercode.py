"""Supercode proxy provider — routes requests through the hosted Supercode server.

Uses the OAuth token from ``~/.better-auth/token.json`` (written by
``supercode login``). Model IDs use the format
``subprovider/modelname`` (e.g. ``concentrateai/deepseek-v4-flash``).

This is an OAuth-backed provider — the token is managed via
:mod:`vtx.llm.oauth.supercode`.
"""

from collections.abc import AsyncIterator
from typing import Any

from ...core.errors import format_error
from ...core.types import (
    AssistantMessage,
    Message,
    StopReason,
    StreamDone,
    StreamError,
    StreamPart,
    TextContent,
    TextPart,
    ThinkingContent,
    ThinkPart,
    ToolCall,
    ToolCallDelta,
    ToolCallStart,
    ToolDefinition,
    ToolResultMessage,
    Usage,
    UserMessage,
)
from ..base import BaseProvider, LLMStream, ProviderConfig
from ..sdk.base import GenerationConfig
from ..sdk.base import Message as SDKMessage
from ..sdk.supercode import SupercodeSDK


class SupercodeProvider(BaseProvider):
    name = "supercode"

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        self._sdk = SupercodeSDK(api_key=config.api_key, base_url=config.base_url)

    def _convert_messages(
        self, messages: list[Message], system_prompt: str | None
    ) -> list[SDKMessage]:
        result: list[SDKMessage] = []
        if system_prompt:
            result.append(SDKMessage(role="system", content=system_prompt))
        for msg in messages:
            if isinstance(msg, UserMessage):
                content = msg.content if isinstance(msg.content, str) else ""
                if not content or content.isspace():
                    raise ValueError("User message content cannot be empty or whitespace-only")
                result.append(SDKMessage(role="user", content=content))
            elif isinstance(msg, AssistantMessage):
                content_parts: list[str] = []
                tool_calls: list[dict[str, Any]] = []
                for item in msg.content:
                    if isinstance(item, TextContent):
                        if item.text.strip():
                            content_parts.append(item.text)
                    elif isinstance(item, ThinkingContent):
                        content_parts.append(f"<think>{item.thinking}</think>")
                    elif isinstance(item, ToolCall):
                        import json

                        tool_calls.append(
                            {
                                "id": item.id,
                                "type": "function",
                                "function": {
                                    "name": item.name,
                                    "arguments": json.dumps(item.arguments),
                                },
                            }
                        )
                metadata = {}
                if tool_calls:
                    metadata["tool_calls"] = tool_calls
                result.append(
                    SDKMessage(
                        role="assistant",
                        content="".join(content_parts) if content_parts else "",
                        metadata=metadata or None,
                    )
                )
            elif isinstance(msg, ToolResultMessage):
                text_parts = [item.text for item in msg.content if isinstance(item, TextContent)]
                content = "\n".join(text_parts) if text_parts else "(no output)"
                result.append(
                    SDKMessage(
                        role="tool", content=content, metadata={"tool_call_id": msg.tool_call_id}
                    )
                )
        return result

    def _convert_tools(self, tools: list[ToolDefinition]) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = []
        for tool in tools:
            if isinstance(tool, dict):
                fn = tool.get("function", tool)
                converted.append(
                    {
                        "type": "function",
                        "function": {
                            "name": fn.get("name", ""),
                            "description": fn.get("description", ""),
                            "parameters": fn.get("parameters", {}),
                        },
                    }
                )
            else:
                converted.append(
                    {
                        "type": "function",
                        "function": {
                            "name": tool.name,
                            "description": tool.description,
                            "parameters": tool.parameters,
                        },
                    }
                )
        return converted

    async def _stream_impl(
        self,
        messages: list[Message],
        *,
        system_prompt: str | None = None,
        tools: list[ToolDefinition] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMStream:
        sdk_messages = self._convert_messages(messages, system_prompt)
        sdk_tools = self._convert_tools(tools) if tools else None

        config = GenerationConfig(
            model=self.config.model,
            temperature=temperature or self.config.temperature or 0.7,
            max_tokens=max_tokens or self.config.max_tokens,
        )

        if sdk_tools:
            raw_stream = await self._sdk.generate_with_tools(
                sdk_messages, sdk_tools, config, stream=True
            )
        else:
            raw_stream = await self._sdk.generate(sdk_messages, config, stream=True)

        llm_stream = LLMStream()
        llm_stream.set_iterator(self._process_stream(raw_stream, llm_stream))
        return llm_stream

    async def _process_stream(
        self, response: Any, llm_stream: LLMStream
    ) -> AsyncIterator[StreamPart]:
        stop_reason: StopReason = StopReason.STOP
        has_tool_calls = False

        try:
            async for chunk in response:
                chunk_type = chunk.get("type")

                if chunk_type == "usage":
                    usage_data = chunk.get("usage", {})
                    llm_stream._usage = Usage(
                        input_tokens=usage_data.get("prompt_tokens", 0)
                        or usage_data.get("input_tokens", 0),
                        output_tokens=usage_data.get("completion_tokens", 0)
                        or usage_data.get("output_tokens", 0),
                        cache_read_tokens=usage_data.get("cache_read_tokens", 0),
                        cache_write_tokens=usage_data.get("cache_write_tokens", 0),
                        reasoning_tokens=usage_data.get("reasoning_tokens", 0),
                    )
                elif chunk_type == "reasoning":
                    yield ThinkPart(
                        think=chunk.get("content", ""),
                        signature=chunk.get("signature", "reasoning_content"),
                    )
                elif chunk_type == "text":
                    yield TextPart(text=chunk.get("content", ""))
                elif chunk_type == "tool_calls":
                    has_tool_calls = True
                    tool_calls = chunk.get("tool_calls", [])
                    for i, tc in enumerate(tool_calls):
                        if isinstance(tc, dict):
                            tc_id = tc.get("id", "")
                            tc_name = tc.get("name", "")
                            tc_args = tc.get("arguments", "")
                        else:
                            tc_id = tc.id
                            tc_name = tc.name
                            tc_args = tc.arguments
                        yield ToolCallStart(id=tc_id, name=tc_name, index=i)
                        yield ToolCallDelta(index=i, arguments_delta=tc_args)
                elif chunk_type == "finish_reason":
                    # The API always sends reason="stop" even when tool calls
                    # happen. Detect tool calls from the event stream instead.
                    if has_tool_calls:
                        stop_reason = StopReason.TOOL_USE
                    else:
                        stop_reason = self._map_finish_reason(chunk.get("finish_reason", ""))
                    usage_data = chunk.get("usage") or {}
                    llm_stream._usage = Usage(
                        input_tokens=usage_data.get("input_tokens", 0),
                        output_tokens=usage_data.get("output_tokens", 0),
                        cache_read_tokens=usage_data.get("cache_read_tokens", 0),
                        cache_write_tokens=usage_data.get("cache_write_tokens", 0),
                        reasoning_tokens=usage_data.get("reasoning_tokens", 0),
                    )

            yield StreamDone(stop_reason=stop_reason)

        except Exception as e:
            yield StreamError(error=format_error(e))

    @staticmethod
    def _map_finish_reason(reason: str) -> StopReason:
        match reason:
            case "stop" | "null" | "":
                return StopReason.STOP
            case "length" | "max_tokens":
                return StopReason.LENGTH
            case "tool_calls":
                return StopReason.TOOL_USE
            case _:
                return StopReason.STOP

    def should_retry_for_error(self, error: Exception) -> bool:
        from ..rate_limit import is_rate_limit_error

        if is_rate_limit_error(error):
            return False
        msg = str(error).lower()
        return any(s in msg for s in ("connection", "timeout", "reset", "500", "502", "503"))

    @staticmethod
    def get_default_model() -> str:
        return "concentrateai/deepseek-v4-flash"

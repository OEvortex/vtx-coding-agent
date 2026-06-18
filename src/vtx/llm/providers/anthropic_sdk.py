"""Anthropic SDK provider - wraps the SDK layer into vtx's BaseProvider interface."""

from collections.abc import AsyncIterator
from typing import Any, ClassVar

from anthropic import APIConnectionError, APIStatusError

from ...core.errors import format_error
from ...core.types import (
    AssistantMessage,
    ImageContent,
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
from ..base import BaseProvider, LLMStream, ProviderConfig, resolve_api_key
from ..sdk.anthropic import AnthropicSDK
from ..sdk.base import GenerationConfig
from ..sdk.base import Message as SDKMessage
from .sanitize import sanitize_surrogates


class AnthropicSDKProvider(BaseProvider):
    name = "anthropic"
    thinking_levels: ClassVar[list[str]] = ["none", "minimal", "low", "medium", "high", "xhigh"]

    def __init__(self, config: ProviderConfig):
        super().__init__(config)

        api_key = resolve_api_key(
            config.api_key,
            env_vars=("ANTHROPIC_API_KEY",),
            base_url=config.base_url,
            auth_mode=config.anthropic_compat_auth_mode,
        )
        if not api_key:
            raise ValueError(
                f"No API key found for {self.name}. "
                "Set ANTHROPIC_API_KEY environment variable or pass api_key in config, "
                'or configure llm.auth.anthropic_compat = "auto"/"none" for local endpoints.'
            )

        self._sdk = AnthropicSDK(api_key=api_key, base_url=config.base_url)

    def _convert_messages(self, messages: list[Message]) -> list[SDKMessage]:
        result: list[SDKMessage] = []
        for msg in messages:
            if isinstance(msg, UserMessage):
                result.append(self._convert_user_message(msg))
            elif isinstance(msg, AssistantMessage):
                result.append(self._convert_assistant_message(msg))
            elif isinstance(msg, ToolResultMessage):
                result.append(self._convert_tool_result(msg))
        return result

    def _convert_user_message(self, msg: UserMessage) -> SDKMessage:
        if isinstance(msg.content, str):
            content = sanitize_surrogates(msg.content)
            if not content or content.isspace():
                raise ValueError("User message content cannot be empty or whitespace-only")
            return SDKMessage(role="user", content=content)

        parts: list[str] = []
        image_parts: list[str] = []
        for item in msg.content:
            if isinstance(item, TextContent):
                text = sanitize_surrogates(item.text)
                if text and not text.isspace():
                    parts.append(text)
            elif isinstance(item, ImageContent):
                image_parts.append(f"data:{item.mime_type};base64,{item.data}")

        content = "\n".join(parts) if parts else ""
        if not content and not image_parts:
            raise ValueError("User message content cannot be empty or whitespace-only")

        return SDKMessage(role="user", content=content, image_parts=image_parts or None)

    def _convert_assistant_message(self, msg: AssistantMessage) -> SDKMessage:
        content_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []

        for item in msg.content:
            if isinstance(item, TextContent):
                if item.text.strip():
                    content_parts.append(sanitize_surrogates(item.text))
            elif isinstance(item, ThinkingContent):
                pass
            elif isinstance(item, ToolCall):
                tool_calls.append({"id": item.id, "name": item.name, "arguments": item.arguments})

        return SDKMessage(
            role="assistant",
            content="".join(content_parts) if content_parts else "",
            metadata={"tool_calls": tool_calls} if tool_calls else None,
        )

    def _convert_tool_result(self, msg: ToolResultMessage) -> SDKMessage:
        text_parts = [item.text for item in msg.content if isinstance(item, TextContent)]
        has_images = any(isinstance(item, ImageContent) for item in msg.content)

        if text_parts:
            content = "\n".join(text_parts)
        elif has_images:
            content = "(see attached image)"
        else:
            content = "(no output)"

        return SDKMessage(
            role="tool",
            content=content,
            metadata={"tool_call_id": msg.tool_call_id, "is_error": msg.is_error},
        )

    def _convert_tools(self, tools: list[ToolDefinition]) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in tools
        ]

    async def _stream_impl(
        self,
        messages: list[Message],
        *,
        system_prompt: str | None = None,
        tools: list[ToolDefinition] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMStream:
        sdk_messages = self._convert_messages(messages)
        sdk_tools = self._convert_tools(tools) if tools else None
        temp = temperature if temperature is not None else self.config.temperature
        max_tok = max_tokens if max_tokens is not None else self.config.max_tokens

        config = GenerationConfig(
            model=self.config.model,
            temperature=temp if temp is not None else 0.7,
            max_tokens=max_tok,
            thinking_level=self.config.thinking_level,
        )

        response = await self._sdk.generate_with_tools(
            sdk_messages, sdk_tools or [], config, stream=True
        )

        llm_stream = LLMStream()
        llm_stream.set_iterator(self._process_stream(response, llm_stream))
        return llm_stream

    async def _process_stream(
        self, response: Any, llm_stream: LLMStream
    ) -> AsyncIterator[StreamPart]:
        stop_reason: StopReason = StopReason.STOP
        current_tool_index: int = -1
        tool_use_blocks: dict[int, dict[str, Any]] = {}

        try:
            async for event in response:
                event_type = event.get("type", "")

                if event_type == "message_start":
                    if event.get("id"):
                        llm_stream._id = event["id"]
                    usage = event.get("usage") or {}
                    llm_stream._usage = Usage(
                        input_tokens=usage.get("input_tokens", 0),
                        output_tokens=usage.get("output_tokens", 0),
                    )
                elif event_type == "content_block_start":
                    block = event.get("content_block", {})
                    if block.get("type") == "tool_use":
                        current_tool_index += 1
                        tool_use_blocks[event.get("index", 0)] = {
                            "id": block.get("id", ""),
                            "name": block.get("name", ""),
                        }
                        yield ToolCallStart(
                            id=block.get("id", ""),
                            name=block.get("name", ""),
                            index=current_tool_index,
                        )
                elif event_type == "content_block_delta":
                    delta = event.get("delta", {})
                    delta_type = delta.get("type", "")

                    if delta_type == "text_delta":
                        yield TextPart(text=delta.get("text", ""))
                    elif delta_type == "thinking_delta":
                        yield ThinkPart(think=delta.get("thinking", ""))
                    elif delta_type == "signature_delta":
                        yield ThinkPart(think="", signature=delta.get("signature", ""))
                    elif delta_type == "input_json_delta":
                        tool_info = tool_use_blocks.get(event.get("index", 0))
                        if tool_info:
                            logical_index = list(tool_use_blocks.keys()).index(
                                event.get("index", 0)
                            )
                            yield ToolCallDelta(
                                index=logical_index, arguments_delta=delta.get("partial_json", "")
                            )
                elif event_type == "message_delta":
                    delta = event.get("delta", {})
                    if delta.get("stop_reason"):
                        stop_reason = self._map_stop_reason(delta["stop_reason"])
                    usage = event.get("usage") or {}
                    if usage and llm_stream._usage:
                        llm_stream._usage = Usage(
                            input_tokens=llm_stream._usage.input_tokens,
                            output_tokens=usage.get("output_tokens", 0),
                            cache_read_tokens=llm_stream._usage.cache_read_tokens,
                            cache_write_tokens=llm_stream._usage.cache_write_tokens,
                        )

            yield StreamDone(stop_reason=stop_reason)

        except Exception as e:
            yield StreamError(error=format_error(e))

    def _map_stop_reason(self, reason: str) -> StopReason:
        match reason:
            case "end_turn":
                return StopReason.STOP
            case "max_tokens":
                return StopReason.LENGTH
            case "tool_use":
                return StopReason.TOOL_USE
            case _:
                return StopReason.STOP

    def should_retry_for_error(self, error: Exception) -> bool:
        from ..rate_limit import is_rate_limit_error

        if is_rate_limit_error(error):
            return False
        if isinstance(error, APIConnectionError):
            return True
        if isinstance(error, APIStatusError):
            return error.status_code >= 500
        return False

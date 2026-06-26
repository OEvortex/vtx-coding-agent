"""OpenAI SDK provider - wraps the SDK layer into vtx's BaseProvider interface."""

from collections.abc import AsyncIterator
from typing import Any, ClassVar

from openai import APIConnectionError, APIError, APIStatusError

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
from ..base import BaseProvider, LLMStream, ProviderConfig, ENV_API_KEY_MAP, resolve_api_key
from ..sdk.base import GenerationConfig
from ..sdk.base import Message as SDKMessage
from ..sdk.openai import OpenAISDK
from .sanitize import sanitize_surrogates


class OpenAISDKProvider(BaseProvider):
    name = "openai"
    # Full OpenAI-style effort enum. The picker filters by per-model
    # capability (Model.supports_thinking) before showing these.
    thinking_levels: ClassVar[list[str]] = ["none", "minimal", "low", "medium", "high", "xhigh"]

    def __init__(self, config: ProviderConfig):
        super().__init__(config)

        provider = (config.provider or "").lower()
        provider_env_var = ENV_API_KEY_MAP.get(provider, "OPENAI_API_KEY")

        api_key = resolve_api_key(
            config.api_key,
            env_vars=(provider_env_var,),
            base_url=config.base_url,
            auth_mode=config.openai_compat_auth_mode,
        )
        if not api_key and config.base_url:
            api_key = self._resolve_dynamic_key_for(config)
        if not api_key:
            raise ValueError(
                f"No API key found for {self.name}. "
                f"Set {provider_env_var} environment variable or pass api_key in config, "
                'or configure llm.auth.openai_compat = "auto"/"none" for local endpoints.'
            )

        self._sdk = OpenAISDK(
            api_key=api_key, base_url=config.base_url, provider_slug=config.provider
        )

    @staticmethod
    def _resolve_dynamic_key_for(config: ProviderConfig) -> str | None:
        from ..oauth.dynamic import get_dynamic_api_key

        return get_dynamic_api_key(config.provider or "")

    def _convert_messages(
        self, messages: list[Message], system_prompt: str | None
    ) -> list[SDKMessage]:
        result: list[SDKMessage] = []

        if system_prompt:
            result.append(SDKMessage(role="system", content=sanitize_surrogates(system_prompt)))

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
        import json

        from ..phase_parser import INLINE_THINK_SIGNATURE

        content_parts: list[str] = []
        metadata: dict[str, Any] = {}
        tool_calls: list[dict[str, Any]] = []
        for item in msg.content:
            if isinstance(item, TextContent):
                if item.text.strip():
                    content_parts.append(sanitize_surrogates(item.text))
            elif isinstance(item, ThinkingContent):
                if item.signature == INLINE_THINK_SIGNATURE:
                    content_parts.append(f"<think>{item.thinking}</think>")
                elif item.signature == "reasoning_content":
                    metadata["reasoning_content"] = item.thinking
            elif isinstance(item, ToolCall):
                tool_calls.append(
                    {
                        "id": item.id,
                        "type": "function",
                        "function": {"name": item.name, "arguments": json.dumps(item.arguments)},
                    }
                )

        if tool_calls:
            metadata["tool_calls"] = tool_calls

        return SDKMessage(
            role="assistant",
            content="".join(content_parts) if content_parts else "",
            metadata=metadata or None,
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
            role="tool", content=content, metadata={"tool_call_id": msg.tool_call_id}
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
        sdk_messages = self._convert_messages(messages, system_prompt)
        sdk_tools = self._convert_tools(tools) if tools else None
        temp = temperature if temperature is not None else self.config.temperature
        max_tok = max_tokens if max_tokens is not None else self.config.max_tokens

        config = GenerationConfig(
            model=self.config.model,
            temperature=temp or 0.7,
            max_tokens=max_tok,
            thinking_level=self.config.thinking_level,
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
                    )
                elif chunk_type == "reasoning":
                    yield ThinkPart(
                        think=chunk.get("content", ""),
                        signature=chunk.get("signature", "reasoning_content"),
                    )
                elif chunk_type == "text" or chunk_type == "content":
                    yield TextPart(text=chunk.get("content", ""))
                elif chunk_type == "tool_calls":
                    tool_calls = chunk.get("tool_calls", [])
                    for i, tc in enumerate(tool_calls):
                        yield ToolCallStart(id=tc.id, name=tc.name, index=i)
                        yield ToolCallDelta(index=i, arguments_delta=tc.arguments)
                elif chunk_type == "finish_reason":
                    stop_reason = self._map_finish_reason(chunk.get("finish_reason", ""))

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
        if isinstance(error, APIConnectionError):
            return True
        if isinstance(error, APIStatusError):
            return error.status_code >= 500
        if isinstance(error, APIError):
            msg = str(error).lower()
            return any(s in msg for s in ("provider returned error", "overloaded", "capacity"))
        return False

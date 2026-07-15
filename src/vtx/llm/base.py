import os
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from ipaddress import ip_address
from typing import Any, ClassVar, Literal, cast
from urllib.parse import urlparse

import httpx

from vtx import config as vtx_config

from ..core.types import (
    Message,
    StreamDone,
    StreamPart,
    TextPart,
    ThinkPart,
    ToolCallDelta,
    ToolCallStart,
    ToolDefinition,
    Usage,
)

DEFAULT_THINKING_LEVELS: list[str] = ["none", "minimal", "low", "medium", "high", "xhigh"]

# Provider-agnostic request/response types.
# Defined here (not in agenite_claw) so vtx core and the agenite-claw gateway
# share a single source of truth without a circular import once agenite_claw
# becomes a separate package.


@dataclass(slots=True)
class ToolCallRequest:
    """A provider-agnostic tool call request."""

    id: str
    name: str
    arguments: dict[str, Any] | str
    extra_content: dict[str, Any] | None = None
    provider_specific_fields: dict[str, Any] | None = None
    function_provider_specific_fields: dict[str, Any] | None = None

    def has_valid_name(self) -> bool:
        return isinstance(self.name, str) and bool(self.name)

    def to_openai_tool_call(self) -> dict[str, Any]:
        args = self.arguments
        if isinstance(args, dict):
            args = json.dumps(args)
        func: dict[str, Any] = {"name": self.name, "arguments": args}
        if self.function_provider_specific_fields:
            func["provider_specific_fields"] = self.function_provider_specific_fields
        payload: dict[str, Any] = {"id": self.id, "type": "function", "function": func}
        if self.extra_content:
            payload["extra_content"] = self.extra_content
        if self.provider_specific_fields:
            payload["provider_specific_fields"] = self.provider_specific_fields
        return payload


@dataclass(slots=True)
class LLMResponse:
    """A provider-agnostic LLM response."""

    content: str | None = None
    tool_calls: list[ToolCallRequest] | None = None
    finish_reason: str = "stop"
    error_kind: str | None = None
    reasoning_content: str | None = None
    thinking_blocks: list[Any] | None = None
    usage: dict[str, int] | None = None

    @property
    def should_execute_tools(self) -> bool:
        return bool(self.tool_calls) and self.finish_reason == "tool_calls"

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


@dataclass(slots=True)
class GenerationSettings:
    """Per-run generation settings."""

    temperature: float | None = None
    max_tokens: int | None = None
    reasoning_effort: str | None = None
    thinking_budget: int | None = None
    stop: list[str] | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    top_p: float | None = None
    seed: int | None = None
    stream: bool = False
    response_format: str | None = None
    json_mode: bool = False
    metadata: dict[str, Any] | None = None
LOCAL_API_KEY_PLACEHOLDER = "vtx-local"
AuthMode = Literal["auto", "required", "none"]

ENV_API_KEY_MAP: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "zhipu": "ZAI_API_KEY",
    "airouter": "AIROUTER_API_KEY",
    "opencode": "OPENCODE_API_KEY",
    "kilo": "KILO_API_KEY",
    "tokenrouter": "TOKENROUTER_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "zyloo": "ZYLOO_API_KEY",
    "opengateway": "OPENGATEWAY_API_KEY",
}


def get_env_api_key(provider: str) -> str | None:
    env_var = ENV_API_KEY_MAP.get(provider)
    return os.environ.get(env_var) if env_var else None


def is_local_base_url(base_url: str | None) -> bool:
    if not base_url:
        return False

    parsed = urlparse(base_url if "://" in base_url else f"https://{base_url}")
    hostname = parsed.hostname
    if hostname is None:
        return False

    normalized = hostname.lower()
    if normalized in {"localhost", "127.0.0.1", "0.0.0.0", "::1"}:
        return True
    if normalized.endswith(".local"):
        return True

    try:
        addr = ip_address(normalized)
    except ValueError:
        return False

    return addr.is_loopback or addr.is_private or addr.is_link_local


def make_http_client() -> httpx.AsyncClient | None:
    # Returns None when verify is required so the SDK uses its own default client.
    if not vtx_config.llm.tls.insecure_skip_verify:
        return None
    return httpx.AsyncClient(
        verify=False, timeout=httpx.Timeout(vtx_config.llm.request_timeout_seconds)
    )


def resolve_api_key(
    explicit_api_key: str | None,
    *,
    env_vars: list[str] | tuple[str, ...] = (),
    base_url: str | None = None,
    auth_mode: AuthMode = "required",
) -> str | None:
    if explicit_api_key:
        return explicit_api_key

    for env_var in env_vars:
        value = os.environ.get(env_var)
        if value:
            return value

    if auth_mode == "none":
        return LOCAL_API_KEY_PLACEHOLDER
    if auth_mode == "auto" and is_local_base_url(base_url):
        return LOCAL_API_KEY_PLACEHOLDER

    return None


@dataclass
class ProviderConfig:
    api_key: str | None = None
    base_url: str | None = None
    model: str = ""
    max_tokens: int | None = None
    temperature: float | None = None
    thinking_level: str = "high"
    provider: str | None = None
    session_id: str | None = None
    openai_compat_auth_mode: AuthMode = "auto"
    anthropic_compat_auth_mode: AuthMode = "auto"
    default_headers: dict[str, str] = field(default_factory=dict)


class LLMStream(AsyncIterator["StreamPart"]):
    """
    Async iterator over stream parts with access to final usage/metadata.

    Usage:
        stream = await provider.stream(messages, tools)
        async for part in stream:
            match part:
                case TextPart(text=t):
                    print(t, end="")
                case ThinkPart(think=t):
                    print(f"[thinking] {t}")
                case ToolCallStart(id=id, name=name):
                    print(f"Tool call: {name}")
                ...

        # After iteration, access final stats
        print(f"Usage: {stream.usage}")
    """

    def __init__(self) -> None:
        self._iterator: AsyncIterator[StreamPart] | None = None
        self._usage: Usage | None = None
        self._id: str | None = None

    def set_iterator(self, iterator: AsyncIterator[StreamPart]) -> None:
        self._iterator = iterator

    def __aiter__(self) -> AsyncIterator[StreamPart]:
        return self

    async def __anext__(self) -> StreamPart:
        if self._iterator is None:
            raise StopAsyncIteration
        return await self._iterator.__anext__()

    async def aclose(self) -> None:
        if self._iterator is None:
            return
        close = getattr(self._iterator, "aclose", None)
        if close is not None:
            await close()

    @property
    def usage(self) -> Usage | None:
        return self._usage

    @property
    def id(self) -> str | None:
        return self._id


class BaseProvider(ABC):
    name: str
    thinking_levels: ClassVar[list[str]] = DEFAULT_THINKING_LEVELS

    def __init__(self, config: ProviderConfig):
        self.config = config

    @property
    def thinking_level(self) -> str:
        return self.config.thinking_level

    def set_thinking_level(self, level: str) -> None:
        if level not in self.thinking_levels:
            raise ValueError(
                f"Invalid thinking level '{level}' for {self.name}. "
                f"Valid levels: {self.thinking_levels}"
            )
        self.config.thinking_level = level

    def cycle_thinking_level(self) -> str:
        levels = self.thinking_levels
        current_idx = (
            levels.index(self.config.thinking_level) if self.config.thinking_level in levels else 0
        )
        next_idx = (current_idx + 1) % len(levels)
        new_level = levels[next_idx]
        self.config.thinking_level = new_level
        return new_level

    async def stream(
        self,
        messages: list[Message],
        *,
        system_prompt: str | None = None,
        tools: list[ToolDefinition] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMStream:
        from .rate_limit import rate_limit_manager

        return await rate_limit_manager.retry_stream(
            self,
            messages,
            system_prompt=system_prompt,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    @abstractmethod
    async def _stream_impl(
        self,
        messages: list[Message],
        *,
        system_prompt: str | None = None,
        tools: list[ToolDefinition] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMStream: ...

    @abstractmethod
    def should_retry_for_error(self, error: Exception) -> bool: ...

    @staticmethod
    def get_default_model() -> str:
        """Return the default model name for this provider."""
        return ""

    @property
    def generation(self) -> Any:
        """Return generation settings (max_tokens, temperature, etc.)."""
        from dataclasses import dataclass

        @dataclass
        class _DefaultGen:
            max_tokens: int = 4096
            temperature: float | None = None
            reasoning_effort: str | None = None
            thinking_budget: int | None = None

        return _DefaultGen()

    @generation.setter
    def generation(self, value: Any) -> None:
        """Allow providers to store custom generation settings."""
        self._generation = value

    @classmethod
    def is_arrearage_response(cls, response: Any) -> bool:
        """Check if the LLM response indicates an arrears/out-of-quota error."""

        finish_reason = getattr(response, "finish_reason", None)
        if finish_reason != "error":
            return False
        error_kind = getattr(response, "error_kind", None) or ""
        text = str(getattr(response, "content", "") or "").lower()
        markers = (
            "insufficient_quota",
            "rate_limit",
            "403",
            "payment",
            "billing",
            "account",
            "quota",
            "exceeded",
            "out of credits",
            "arrear",
            "429",
        )
        if error_kind and any(m in error_kind.lower() for m in markers):
            return True
        return bool(text and any(m in text for m in markers))

    async def chat_with_retry(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
        retry_mode: str = "standard",
        on_retry_wait: Any = None,
        **kwargs: Any,
    ) -> Any:
        """Non-streaming chat completion with retry. Consumes stream internally."""
        from ..core.types import ToolDefinition

        converted_messages = self._convert_dict_messages(messages)
        system_prompt = None
        if converted_messages and converted_messages[0].get("role") == "system":
            system_prompt = converted_messages.pop(0).get("content")

        tool_defs = None
        if tools:
            tool_defs = [
                ToolDefinition(
                    name=t["function"]["name"],
                    description=t["function"].get("description", ""),
                    parameters=t["function"].get("parameters", {}),
                )
                for t in tools
                if "function" in t
            ]

        stream = await self.stream(
            cast(list[Message], converted_messages),
            system_prompt=system_prompt,
            tools=tool_defs,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        content_parts: list[str] = []
        thinking_parts: list[str] = []
        tool_calls_raw: dict[int, dict[str, Any]] = {}
        stop_reason = "stop"
        usage_dict: dict[str, int] = {}

        async for part in stream:
            if isinstance(part, TextPart):
                content_parts.append(part.text)
            elif isinstance(part, ThinkPart):
                thinking_parts.append(part.think)
            elif isinstance(part, ToolCallStart):
                tool_calls_raw[part.index] = {"id": part.id, "name": part.name, "arguments": ""}
            elif isinstance(part, ToolCallDelta):
                if part.index in tool_calls_raw:
                    existing = tool_calls_raw[part.index]["arguments"]
                    tool_calls_raw[part.index]["arguments"] = existing + (
                        part.arguments_delta or ""
                    )
            elif isinstance(part, StreamDone):
                usage_dict = {
                    "prompt_tokens": getattr(part, "input_tokens", 0),
                    "completion_tokens": getattr(part, "output_tokens", 0),
                    "total_tokens": getattr(part, "input_tokens", 0)
                    + getattr(part, "output_tokens", 0),
                }

        if stream.usage:
            usage_dict = {
                "prompt_tokens": stream.usage.input_tokens,
                "completion_tokens": stream.usage.output_tokens,
                "total_tokens": stream.usage.input_tokens + stream.usage.output_tokens,
            }

        import json as _json

        tool_call_requests = []
        for idx in sorted(tool_calls_raw):
            tc = tool_calls_raw[idx]
            try:
                args = _json.loads(tc["arguments"]) if tc["arguments"] else {}
            except _json.JSONDecodeError:
                args = {"raw": tc["arguments"]}
            tool_call_requests.append(
                ToolCallRequest(id=tc["id"], name=tc["name"], arguments=args)
            )

        reasoning_content = "\n".join(thinking_parts) if thinking_parts else None
        final_content = "\n".join(content_parts) if content_parts else None

        return LLMResponse(
            content=final_content,
            tool_calls=tool_call_requests or None,
            finish_reason=stop_reason,
            reasoning_content=reasoning_content,
            usage=usage_dict or None,
        )

    def _convert_dict_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return messages

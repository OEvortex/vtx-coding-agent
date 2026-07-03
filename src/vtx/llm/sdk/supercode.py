"""Supercode proxy SDK — translates OpenAI-compatible messages to Supercode's custom NDJSON API.

Uses the OAuth token from ``~/.better-auth/token.json`` (written by the
Supercode CLI's ``login`` command).
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncGenerator
from typing import Any

import httpx

from ..oauth.supercode import load_supercode_credentials
from .base import BaseLLMSDK, GenerationConfig, GenerationResponse, Message, ToolCall

logger = logging.getLogger(__name__)

_BASE_URL = "https://supercode-8w7e.onrender.com"
_CHAT_ENDPOINT = "/api/ai/chat"


def _parse_supercode_model(model_id: str) -> tuple[str, str]:
    """Split ``provider/model`` into (provider, model).

    If no ``/`` separator is found, defaults to ``concentrateai``.
    """
    if "/" in model_id:
        parts = model_id.split("/", 1)
        return parts[0], parts[1]
    return "concentrateai", model_id


def _get_token() -> str:
    creds = load_supercode_credentials()
    if creds is None:
        raise ValueError("No Supercode token found. Run `supercode login` first.")
    return creds.token


def _extract_usage(raw: dict[str, Any]) -> dict[str, int]:
    """Extract all token counts from Supercode's nested usage format.

    Handles:
    - Flat fields: inputTokens, outputTokens, totalTokens
    - Nested: inputTokenDetails.noCacheTokens, inputTokenDetails.cacheReadTokens,
      inputTokenDetails.cacheWriteTokens
    - Nested: outputTokenDetails.textTokens, outputTokenDetails.reasoningTokens
    - Flat aliases: reasoningTokens, cachedInputTokens
    """
    usage: dict[str, int] = {
        "input_tokens": raw.get("inputTokens", 0),
        "output_tokens": raw.get("outputTokens", 0),
        "total_tokens": raw.get("totalTokens", 0),
    }

    input_details = raw.get("inputTokenDetails") or {}
    if isinstance(input_details, dict):
        usage["cache_read_tokens"] = input_details.get("cacheReadTokens", 0)
        usage["cache_write_tokens"] = input_details.get("cacheWriteTokens", 0)

    output_details = raw.get("outputTokenDetails") or {}
    if isinstance(output_details, dict):
        usage["reasoning_tokens"] = output_details.get("reasoningTokens", 0)

    # Some providers emit flat reasoningTokens / cachedInputTokens
    if not usage.get("reasoning_tokens"):
        usage["reasoning_tokens"] = raw.get("reasoningTokens", 0)
    if not usage.get("cache_read_tokens"):
        usage["cache_read_tokens"] = raw.get("cachedInputTokens", 0)

    return usage


class SupercodeSDK(BaseLLMSDK):
    """SDK that wraps the Supercode proxy API.

    The Supercode API uses a custom NDJSON streaming format (not standard SSE).
    This adapter translates between Vtx's OpenAI-compatible message format and
    Supercode's expected payload.
    """

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        self._token = api_key or _get_token()
        self._base_url = (base_url or _BASE_URL).rstrip("/")
        super().__init__(api_key=self._token, base_url=self._base_url)

    @property
    def client(self) -> Any:
        return None

    async def generate(
        self, messages: list[Message], config: GenerationConfig, stream: bool = False
    ) -> GenerationResponse | AsyncGenerator:
        return await self._generate(messages, None, config, stream)

    async def generate_with_tools(
        self,
        messages: list[Message],
        tools: list[dict],
        config: GenerationConfig,
        stream: bool = False,
    ) -> GenerationResponse | AsyncGenerator:
        return await self._generate(messages, tools, config, stream)

    async def _generate(
        self,
        messages: list[Message],
        tools: list[dict] | None,
        config: GenerationConfig,
        stream: bool = False,
    ) -> GenerationResponse | AsyncGenerator:
        model_id = config.model or "concentrateai/deepseek-v4-flash"
        provider, actual_model = _parse_supercode_model(model_id)

        payload_messages = self._messages_to_supercode(messages)

        payload: dict[str, Any] = {
            "provider": provider,
            "model": actual_model,
            "messages": payload_messages,
        }

        if tools:
            supercode_tools: dict[str, Any] = {}
            for t in tools:
                fn = t.get("function", {})
                name = fn.get("name", "")
                if name:
                    supercode_tools[name] = {
                        "description": fn.get("description", ""),
                        "parameters": fn.get("parameters", {}),
                        "inputSchema": fn.get("parameters", {}),
                    }
            if supercode_tools:
                payload["tools"] = supercode_tools

        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self._token}"}

        if stream:
            return self._stream_chat(payload, headers, config)
        else:
            return await self._non_streaming_chat(payload, headers, config)

    def _messages_to_supercode(self, messages: list[Message]) -> list[dict[str, Any]]:
        """Convert Vtx SDK Messages to Supercode's message format.

        Per the OpenAI spec (which Supercode follows), assistant messages
        with ``tool_calls`` and no text content should have ``content: null``,
        not ``content: ""``.  Some providers reject empty-string content
        on tool-call assistant blocks.
        """
        result = []
        for msg in messages:
            has_tool_calls = msg.metadata and "tool_calls" in msg.metadata
            entry: dict[str, Any] = {"role": msg.role}
            if has_tool_calls and not msg.content:
                # OpenAI format: null content when assistant only sends tool calls
                entry["content"] = None
            else:
                entry["content"] = msg.content
            if msg.metadata:
                if "tool_calls" in msg.metadata:
                    entry["tool_calls"] = msg.metadata["tool_calls"]
                if "tool_call_id" in msg.metadata:
                    entry["tool_call_id"] = msg.metadata["tool_call_id"]
            result.append(entry)
        return result

    async def _stream_chat(
        self, payload: dict[str, Any], headers: dict[str, str], config: GenerationConfig
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream from Supercode and yield Vtx-compatible chunks."""
        url = f"{self._base_url}{_CHAT_ENDPOINT}"
        async with httpx.AsyncClient(timeout=120) as client:
            try:
                async with client.stream("POST", url, json=payload, headers=headers) as response:
                    response.raise_for_status()
                    finish_reason: str | None = None
                    tool_calls_acc: dict[int, dict[str, Any]] = {}

                    async for line in response.aiter_lines():
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        match event.get("type"):
                            case "error":
                                msg = event.get("error", "Unknown Supercode API error")
                                raise RuntimeError(f"Supercode API error: {msg}")
                            case "text":
                                yield {"type": "text", "content": event.get("content", "")}
                            case "reasoning":
                                yield {
                                    "type": "reasoning",
                                    "content": event.get("content", ""),
                                    "signature": "reasoning_content",
                                }
                            case "tool-call":
                                idx = len(tool_calls_acc)
                                tc_id = event.get("toolCallId", f"call_{time.time_ns()}_{idx}")
                                tc_name = event.get("toolName", "")
                                tc_args = json.dumps(event.get("args", {}))
                                tool_calls_acc[idx] = {
                                    "id": tc_id,
                                    "name": tc_name,
                                    "arguments": tc_args,
                                }
                                yield {
                                    "type": "tool_calls",
                                    "tool_calls": [
                                        ToolCall(id=tc_id, name=tc_name, arguments=tc_args)
                                    ],
                                    "index": idx,
                                }
                            case "finish":
                                finish_reason = event.get("reason", "stop")
                                usage_raw = event.get("usage", {}) or {}
                                yield {"type": "usage", "usage": _extract_usage(usage_raw)}
                                yield {
                                    "type": "finish_reason",
                                    "finish_reason": finish_reason,
                                    "usage": _extract_usage(usage_raw),
                                }

                    if finish_reason is None:
                        yield {"type": "finish_reason", "finish_reason": "stop"}
            except httpx.HTTPStatusError as e:
                body = await e.response.aread()
                raise RuntimeError(
                    f"Supercode API error {e.response.status_code}: "
                    f"{body.decode(errors='replace')}"
                ) from e

    async def _non_streaming_chat(
        self, payload: dict[str, Any], headers: dict[str, str], config: GenerationConfig
    ) -> GenerationResponse:
        collected_content = ""
        reasoning_content = ""
        finish_reason = "stop"
        usage_dict: dict[str, int] | None = None
        tool_calls: list[ToolCall] = []

        async for chunk in self._stream_chat(payload, headers, config):
            if chunk.get("type") == "text":
                collected_content += chunk.get("content", "")
            elif chunk.get("type") == "reasoning":
                reasoning_content += chunk.get("content", "")
            elif chunk.get("type") == "tool_calls":
                tool_calls.extend(chunk.get("tool_calls", []))
            elif chunk.get("type") == "finish_reason":
                finish_reason = chunk.get("finish_reason", "stop")
                usage_dict = chunk.get("usage")

        return GenerationResponse(
            content=collected_content,
            model=payload.get("model", ""),
            finish_reason=finish_reason,
            tool_calls=tool_calls if tool_calls else None,
            usage=usage_dict,
            reasoning_content=reasoning_content,
        )

    def get_available_models(self) -> list[str]:
        return [
            "concentrateai/deepseek-v4-flash",
            "concentrateai/glm-5.2",
            "concentrateai/glm-5.1",
            "concentrateai/kimi-k2-6",
            "concentrateai/minimax-m3",
            "google/gemini-2.5-flash",
            "google/gemini-2.5-pro",
            "nvidia/minimaxai/minimax-m3",
            "nvidia/deepseek-ai/deepseek-v4-flash",
            "nvidia/meta/llama-3.3-70b-instruct",
            "openrouter/openai/gpt-oss-120b:free",
            "openrouter/deepseek/deepseek-v4-flash",
            "openrouter/minimax/minimax-m3",
            "openrouter/z-ai/glm-5.1",
            "openrouter/moonshotai/kimi-k2.6",
        ]

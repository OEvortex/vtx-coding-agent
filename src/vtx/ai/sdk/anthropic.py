"""Anthropic-native SDK. Direct HTTP via httpx."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncGenerator
from typing import Any

import httpx

from vtx.ai.provider_hooks import prepare_request
from vtx.ai.sdk.base import BaseLLMSDK, GenerationConfig, GenerationResponse, Message, ToolCall
from vtx.ai.thinking import ANTHROPIC_MESSAGES, resolve_reasoning_params

logger = logging.getLogger(__name__)

ANTHROPIC_API_ROOT = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"
_RETRY_BASE_DELAY = 1.0
_MAX_RETRIES = 3
_DEFAULT_MAX_TOKENS = 16384


def _is_anthropic_api(base_url: str | None) -> bool:
    if not base_url:
        return True
    lower = base_url.lower()
    return "api.anthropic.com" in lower


def _is_transient(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500 or exc.response.status_code == 429
    if isinstance(
        exc, (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout, httpx.RemoteProtocolError)
    ):
        return True
    msg = str(exc).lower()
    return any(
        s in msg
        for s in (
            "connection",
            "timeout",
            "timed out",
            "reset",
            "broken pipe",
            "network",
            "unavailable",
            "bad gateway",
        )
    )


def _content_to_anthropic(content: str | list[dict[str, Any]]) -> str | list[dict[str, Any]]:
    if isinstance(content, str):
        return content
    out: list[dict[str, Any]] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        kind = part.get("type")
        if kind == "text":
            out.append({"type": "text", "text": part.get("text", "")})
        elif kind == "image_url":
            url = (part.get("image_url") or {}).get("url", "")
            if url.startswith("data:"):
                try:
                    header, b64 = url.split(",", 1)
                except ValueError:
                    continue
                media = header[len("data:") :].split(";", 1)[0]
                out.append(
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": media, "data": b64},
                    }
                )
    return out


def _messages_to_anthropic(messages: list[Message]) -> tuple[str | None, list[dict[str, Any]]]:
    system_parts: list[str] = []
    converted: list[dict[str, Any]] = []
    for m in messages:
        role = (m.role or "").lower()
        if role == "system":
            text = m.content if isinstance(m.content, str) else str(m.content or "")
            if text:
                system_parts.append(text)
            continue
        if role not in ("user", "assistant"):
            if role == "tool" and converted and converted[-1]["role"] == "user":
                converted[-1]["content"] = (
                    converted[-1]["content"]
                    if isinstance(converted[-1]["content"], list)
                    else [{"type": "text", "text": converted[-1]["content"]}]
                )
                converted[-1]["content"].append(
                    {
                        "type": "tool_result",
                        "tool_use_id": m.metadata.get("tool_call_id", "") if m.metadata else "",
                        "content": m.content
                        if isinstance(m.content, str)
                        else str(m.content or ""),
                    }
                )
            continue
        # Assistant: replay thinking blocks (with signatures) before text content
        # so tool-use continuity is preserved across turns (Anthropic requires
        # thinking blocks to be replayed verbatim when thinking is enabled).
        thinking_blocks: list[dict[str, Any]] = []
        if role == "assistant" and m.metadata and m.metadata.get("thinking_blocks"):
            for tb in m.metadata["thinking_blocks"]:
                if not isinstance(tb, dict):
                    continue
                if tb.get("type") == "redacted_thinking":
                    thinking_blocks.append(
                        {"type": "redacted_thinking", "data": tb.get("data", "")}
                    )
                elif tb.get("type") == "thinking":
                    block: dict[str, Any] = {
                        "type": "thinking",
                        "thinking": tb.get("thinking", ""),
                    }
                    sig = tb.get("signature")
                    if sig:
                        block["signature"] = sig
                    thinking_blocks.append(block)
        anthropic_content = _content_to_anthropic(m.content if m.content is not None else "")
        if thinking_blocks:
            if isinstance(anthropic_content, str):
                anthropic_content = (
                    [{"type": "text", "text": anthropic_content}] if anthropic_content else []
                )
            # thinking blocks must come first
            anthropic_content = thinking_blocks + (
                anthropic_content if isinstance(anthropic_content, list) else []
            )
        converted.append({"role": role, "content": anthropic_content})
    merged: list[dict[str, Any]] = []
    for msg in converted:
        if merged and merged[-1]["role"] == msg["role"]:
            prev = merged[-1]["content"]
            cur = msg["content"]
            if isinstance(prev, str):
                prev = [{"type": "text", "text": prev}]
            if isinstance(cur, str):
                cur = [{"type": "text", "text": cur}]
            merged[-1]["content"] = prev + cur
        else:
            merged.append(msg)
    if merged and merged[0]["role"] != "user":
        merged.insert(0, {"role": "user", "content": [{"type": "text", "text": "(continue)"}]})
    system = "\n\n".join(system_parts) if system_parts else None
    return system, merged


def _tools_to_anthropic(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    if not tools:
        return None
    out: list[dict[str, Any]] = []
    for t in tools:
        if t.get("type") == "function" and "function" in t:
            fn = t["function"]
            out.append(
                {
                    "name": fn.get("name", ""),
                    "description": fn.get("description", ""),
                    "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
                }
            )
        else:
            out.append(t)
    return out


def _parse_anthropic_response(data: dict[str, Any], model: str) -> GenerationResponse:
    content = data.get("content", [])
    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    thinking_parts: list[str] = []
    for block in content:
        kind = block.get("type")
        if kind == "text":
            text_parts.append(block.get("text", ""))
        elif kind == "tool_use":
            tool_calls.append(
                ToolCall(
                    id=block.get("id", ""),
                    name=block.get("name", ""),
                    arguments=json.dumps(block.get("input", {})),
                )
            )
        elif kind == "thinking":
            thinking_parts.append(block.get("thinking", ""))
        elif kind == "redacted_thinking":
            thinking_parts.append("[Reasoning redacted]")
    usage = data.get("usage", {}) or {}
    usage_dict: dict[str, Any] | None = None
    if usage:
        usage_dict = {
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
            "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
        }
        if usage.get("cache_creation_input_tokens"):
            usage_dict["cache_write_tokens"] = usage["cache_creation_input_tokens"]
        if usage.get("cache_read_input_tokens"):
            usage_dict["cache_read_tokens"] = usage["cache_read_input_tokens"]
        # Newer SDK nests cache_creation.ephemeral_*
        cc = usage.get("cache_creation") or {}
        if isinstance(cc, dict) and cc.get("ephemeral_1h_input_tokens"):
            usage_dict["cache_write_tokens"] = cc["ephemeral_1h_input_tokens"]
    return GenerationResponse(
        content="\n".join(text_parts),
        model=model,
        finish_reason=data.get("stop_reason"),
        tool_calls=tool_calls or None,
        usage=usage_dict,
        reasoning_content="\n".join(thinking_parts),
    )


class AnthropicSDK(BaseLLMSDK):
    def __init__(self, api_key: str, base_url: str | None = None, **_: Any):
        url: str = base_url or ANTHROPIC_API_ROOT
        super().__init__(api_key, url)
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:  # pyright: ignore[reportIncompatibleMethodOverride]
        if self._client is None:
            assert self.base_url is not None
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(60.0, read=300.0),
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": ANTHROPIC_VERSION,
                    "content-type": "application/json",
                },
            )
        return self._client

    def _resolve_model(self, config: GenerationConfig) -> str:
        model = (config.model or "").strip() or os.getenv("VTX_MODEL", "").strip()
        if model:
            return model
        return "claude-3-5-sonnet-latest"

    def _build_payload(
        self,
        messages: list[Message],
        config: GenerationConfig,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        system, converted = _messages_to_anthropic(messages)
        model = self._resolve_model(config)
        max_tokens = config.max_tokens if config.max_tokens is not None else _DEFAULT_MAX_TOKENS
        payload: dict[str, Any] = {"model": model, "max_tokens": max_tokens, "messages": converted}
        # System as block with cache_control (pi parity) — only for official API
        if system:
            if _is_anthropic_api(self.base_url):
                payload["system"] = [
                    {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
                ]
            else:
                payload["system"] = system
        # Suppress temperature/top_p when thinking is enabled — Opus 4.7+ rejects
        # temperature != 1 and top_p/top_k with 400 (docs + pi compat flag).
        thinking_params = resolve_reasoning_params(
            ANTHROPIC_MESSAGES,
            config.thinking_level,
            level_map=config.thinking_level_map,
            max_tokens=config.max_tokens,
        )
        has_thinking = bool(thinking_params.get("thinking"))
        if not has_thinking:
            if config.temperature is not None:
                payload["temperature"] = config.temperature
            if config.top_p is not None:
                payload["top_p"] = config.top_p
        if config.stop_sequences:
            payload["stop_sequences"] = config.stop_sequences
        anthropic_tools = _tools_to_anthropic(tools)
        if anthropic_tools:
            # Cache the last tool definition (pi places cache_control on last tool)
            if _is_anthropic_api(self.base_url) and anthropic_tools:
                anthropic_tools[-1] = {
                    **anthropic_tools[-1],
                    "cache_control": {"type": "ephemeral"},
                }
            payload["tools"] = anthropic_tools
            tc = config.tool_choice
            if isinstance(tc, str) and tc in ("auto", "any", "none"):
                payload["tool_choice"] = {"type": tc}
            elif isinstance(tc, dict):
                payload["tool_choice"] = tc
        # Cache the last user message's last content block (pi parity)
        if _is_anthropic_api(self.base_url) and payload["messages"]:
            last_msg = payload["messages"][-1]
            content = last_msg.get("content")
            if isinstance(content, list) and content:
                last_block = content[-1]
                if isinstance(last_block, dict) and "cache_control" not in last_block:
                    last_block["cache_control"] = {"type": "ephemeral"}
        # Apply thinking last so it can override max_tokens-derived budget
        payload.update(thinking_params)
        return payload

    @staticmethod
    def _apply_thinking_payload(payload: dict[str, Any], config: GenerationConfig) -> None:
        """Translate ``config.thinking_level`` into the Anthropic-native
        ``thinking`` block via the shared resolver
        (:func:`vtx.ai.thinking.resolve_reasoning_params`).

        Uses ``type: "enabled" + budget_tokens`` — the documented form
        across current Claude models. Budgets come from the shared
        ``ANTHROPIC_BUDGETS`` table (minimal=1024 … xhigh=16384) and are
        clamped to stay strictly below ``max_tokens``. Older models that
        reject manual thinking return 400, which surfaces normally.

        Reference:
          https://platform.claude.com/docs/en/build-with-claude/extended-thinking
        """
        payload.update(
            resolve_reasoning_params(
                ANTHROPIC_MESSAGES,
                config.thinking_level,
                level_map=config.thinking_level_map,
                max_tokens=payload.get("max_tokens"),
            )
        )

    async def generate(
        self, messages: list[Message], config: GenerationConfig, stream: bool = False
    ) -> GenerationResponse | AsyncGenerator:
        if stream:
            return self._generate_stream(messages, config)
        return await self._generate_blocking(messages, config)

    async def _generate_blocking(
        self,
        messages: list[Message],
        config: GenerationConfig,
        tools: list[dict[str, Any]] | None = None,
    ) -> GenerationResponse:
        payload = self._build_payload(messages, config, tools)
        extra_headers, payload = await prepare_request(provider="anthropic", payload=payload)
        request_kwargs: dict[str, Any] = {"json": payload}
        if extra_headers:
            request_kwargs["headers"] = extra_headers
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                resp = await self.client.post("/v1/messages", **request_kwargs)
                if resp.status_code >= 400:
                    if _is_transient(
                        httpx.HTTPStatusError("err", request=resp.request, response=resp)
                    ):
                        raise httpx.HTTPStatusError("err", request=resp.request, response=resp)
                    body = resp.text
                    raise RuntimeError(f"Anthropic API error {resp.status_code}: {body[:300]}")
                data = resp.json()
                return _parse_anthropic_response(data, payload["model"])
            except Exception as exc:
                last_exc = exc
                if not _is_transient(exc) or attempt == _MAX_RETRIES - 1:
                    raise
                delay = _RETRY_BASE_DELAY * (2**attempt)
                logger.warning(
                    "Anthropic transient error (attempt %d/%d), retrying in %.1fs: %s",
                    attempt + 1,
                    _MAX_RETRIES,
                    delay,
                    str(exc)[:200],
                )
                await asyncio.sleep(delay)
        if last_exc:
            raise last_exc
        raise RuntimeError("unreachable")

    async def _generate_stream(
        self,
        messages: list[Message],
        config: GenerationConfig,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        payload = self._build_payload(messages, config, tools)
        payload["stream"] = True
        extra_headers, payload = await prepare_request(provider="anthropic", payload=payload)
        stream_kwargs: dict[str, Any] = {"json": payload}
        if extra_headers:
            stream_kwargs["headers"] = extra_headers
        try:
            async with self.client.stream("POST", "/v1/messages", **stream_kwargs) as resp:
                if resp.status_code >= 400:
                    body = await resp.aread()
                    raise RuntimeError(
                        f"Anthropic API error {resp.status_code}: "
                        f"{body.decode('utf-8', errors='replace')[:300]}"
                    )
                # Passthrough raw SSE events — the provider layer (AnthropicSDKProvider)
                # owns the StreamPart mapping (TextPart/ThinkPart/ToolCall*), including
                # thinking_delta / signature_delta / redacted_thinking handling.
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    if not line.startswith("data: "):
                        continue
                    raw = line[len("data: ") :].strip()
                    if not raw or raw == "[DONE]":
                        continue
                    try:
                        ev = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    yield ev
        except (GeneratorExit, asyncio.CancelledError):
            return

    async def generate_with_tools(
        self,
        messages: list[Message],
        tools: list[dict],
        config: GenerationConfig,
        stream: bool = False,
    ) -> GenerationResponse | AsyncGenerator:
        if stream:
            return self._generate_stream(messages, config, tools=tools)
        return await self._generate_blocking(messages, config, tools=tools)

    def get_available_models(self) -> list[str]:
        return ["claude-3-5-sonnet-latest", "claude-3-5-haiku-latest", "claude-3-opus-latest"]

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

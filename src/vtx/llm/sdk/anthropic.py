"""Anthropic-native SDK. Direct HTTP via httpx."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncGenerator
from typing import Any

import httpx

from .base import BaseLLMSDK, GenerationConfig, GenerationResponse, Message, ToolCall

logger = logging.getLogger(__name__)

ANTHROPIC_API_ROOT = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"
_MAX_TOKENS_DEFAULT = 4096
_RETRY_BASE_DELAY = 1.0
_MAX_RETRIES = 3


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
        converted.append(
            {
                "role": role,
                "content": _content_to_anthropic(m.content if m.content is not None else ""),
            }
        )
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
    usage = data.get("usage", {})
    return GenerationResponse(
        content="\n".join(text_parts),
        model=model,
        finish_reason=data.get("stop_reason"),
        tool_calls=tool_calls or None,
        usage={
            "prompt_tokens": usage.get("input_tokens", 0),
            "completion_tokens": usage.get("output_tokens", 0),
            "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
        }
        if usage
        else None,
        reasoning_content="\n".join(thinking_parts),
    )


class AnthropicSDK(BaseLLMSDK):
    def __init__(self, api_key: str, base_url: str | None = None, **_: Any):
        url: str = base_url or ANTHROPIC_API_ROOT
        super().__init__(api_key, url)
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
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
        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": config.max_tokens or _MAX_TOKENS_DEFAULT,
            "messages": converted,
        }
        if system:
            payload["system"] = system
        if config.temperature is not None:
            payload["temperature"] = config.temperature
        if config.top_p is not None:
            payload["top_p"] = config.top_p
        if config.stop_sequences:
            payload["stop_sequences"] = config.stop_sequences
        anthropic_tools = _tools_to_anthropic(tools)
        if anthropic_tools:
            payload["tools"] = anthropic_tools
            tc = config.tool_choice
            if isinstance(tc, str) and tc in ("auto", "any", "none"):
                payload["tool_choice"] = {"type": tc}
            elif isinstance(tc, dict):
                payload["tool_choice"] = tc
        self._apply_thinking_payload(payload, config)
        return payload

    @staticmethod
    def _apply_thinking_payload(payload: dict[str, Any], config: GenerationConfig) -> None:
        """Translate ``config.thinking_level`` into the Anthropic-native
        ``thinking`` block.

        Uses manual ``type: "enabled" + budget_tokens`` for every
        level, which is the documented form across current Claude
        models (Sonnet 4.5 / Opus 4.5 / Sonnet 4.6 / Opus 4.6).
        Older models that don't accept manual thinking will return
        400, which the user sees normally.

        Level -> budget_tokens (Anthropic minimum is 1024 and
        budget_tokens must be strictly less than max_tokens):
          - "minimal" -> 1024
          - "low"     -> 2048
          - "medium"  -> 4096
          - "high"    -> 8192
          - "xhigh"   -> 16384

        Reference:
          https://platform.claude.com/docs/en/build-with-claude/extended-thinking
        """
        level = config.thinking_level
        if level is None or level == "none":
            # Anthropic defaults to non-thinking when the field is
            # omitted, so "none" is implemented as no field at all.
            return

        manual_budget: dict[str, int] = {
            "minimal": 1024,
            "low": 2048,
            "medium": 4096,
            "high": 8192,
            "xhigh": 16384,
        }
        budget = manual_budget.get(level)
        if budget is None:
            return
        payload["thinking"] = {"type": "enabled", "budget_tokens": budget}

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
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                resp = await self.client.post("/v1/messages", json=payload)
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
        self, messages: list[Message], config: GenerationConfig
    ) -> AsyncGenerator[dict[str, Any], None]:
        payload = self._build_payload(messages, config)
        payload["stream"] = True
        async with self.client.stream("POST", "/v1/messages", json=payload) as resp:
            if resp.status_code >= 400:
                body = await resp.aread()
                raise RuntimeError(
                    f"Anthropic API error {resp.status_code}: "
                    f"{body.decode('utf-8', errors='replace')[:300]}"
                )
            content_buf: list[str] = []
            tool_calls: dict[int, dict[str, Any]] = {}
            input_tokens = 0
            output_tokens = 0
            current_block: dict[str, Any] | None = None
            async for line in resp.aiter_lines():
                if not line:
                    continue
                if line.startswith("data: "):
                    raw = line[len("data: ") :]
                    if raw.strip() == "[DONE]":
                        break
                    try:
                        ev = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    et = ev.get("type", "")
                    if et == "message_start":
                        usage = (ev.get("message") or {}).get("usage") or {}
                        input_tokens = usage.get("input_tokens", 0)
                    elif et == "content_block_start":
                        current_block = ev.get("content_block", {})
                    elif et == "content_block_delta":
                        delta = ev.get("delta", {})
                        if delta.get("type") == "text_delta":
                            text = delta.get("text", "")
                            content_buf.append(text)
                            yield {"type": "content", "content": text}
                        elif delta.get("type") == "input_json_delta":
                            idx = ev.get("index", 0)
                            tc = tool_calls.setdefault(
                                idx,
                                {
                                    "id": (current_block or {}).get("id", ""),
                                    "name": (current_block or {}).get("name", ""),
                                    "arguments": "",
                                },
                            )
                            tc["arguments"] += delta.get("partial_json", "")
                    elif et == "content_block_stop":
                        if current_block and current_block.get("type") == "tool_use":
                            idx = ev.get("index", 0)
                            tool_calls.setdefault(
                                idx,
                                {
                                    "id": current_block.get("id", ""),
                                    "name": current_block.get("name", ""),
                                    "arguments": "",
                                },
                            )
                    elif et == "message_delta":
                        usage = ev.get("usage", {})
                        output_tokens = usage.get("output_tokens", 0)
                    elif et == "message_stop":
                        break
                    current_block = None
        if tool_calls:
            calls = [
                ToolCall(id=tc["id"], name=tc["name"], arguments=tc["arguments"] or "{}")
                for tc in tool_calls.values()
            ]
            yield {"type": "tool_calls", "tool_calls": calls}
        if input_tokens or output_tokens:
            yield {
                "type": "usage",
                "usage": {
                    "prompt_tokens": input_tokens,
                    "completion_tokens": output_tokens,
                    "total_tokens": input_tokens + output_tokens,
                },
            }

    async def generate_with_tools(
        self,
        messages: list[Message],
        tools: list[dict],
        config: GenerationConfig,
        stream: bool = False,
    ) -> GenerationResponse | AsyncGenerator:
        return await self._generate_blocking(messages, config, tools=tools)

    def get_available_models(self) -> list[str]:
        return ["claude-3-5-sonnet-latest", "claude-3-5-haiku-latest", "claude-3-opus-latest"]

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

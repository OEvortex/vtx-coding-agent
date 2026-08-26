"""OpenAI Responses API adapter.

Transported through the **official ``openai`` package**
(``AsyncOpenAI().responses.create(...)``), adapted to vtx's chunk
vocabulary:

- conversation history lowers to ``input`` items: ``{role: system}``,
  user/assistant ``message`` items, ``function_call`` items for assistant
  tool calls, and ``function_call_output`` items keyed by ``call_id``
- system prompts ride as input items (the schema keeps them inline)
- tools are flattened ``{"type": "function", name, description,
  parameters}``
- reasoning effort resolves through the shared per-model map:
  clamp the requested level against the model's supported levels, look
  up ``thinking_level_map[level] ?? level``, and emit ``reasoning:
  {effort: ...}`` only when the model reasons and the level isn't "off"
- ``store: false`` by default (stateless sessions)
- streaming rides the SDK's ``AsyncStream[ResponseStreamEvent]``; typed
  events are dumped back to dicts and dispatched through the stream state
  machine: ``output_text.delta``, ``reasoning_*_delta``,
  ``function_call_arguments.delta/.done``, ``output_item.done``
  (function_call -> tool_calls chunk), ``response.completed/incomplete``
  (usage + finish reason), ``response.failed``/``error``
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from collections.abc import AsyncGenerator
from typing import Any

from openai import AsyncOpenAI

from vtx.ai.sdk.base import BaseLLMSDK, GenerationConfig, GenerationResponse, Message, ToolCall
from vtx.ai.thinking import clamp_thinking_level, get_supported_thinking_levels

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "gpt-5"
ADAPTER = "openai-responses"


class OpenAIResponsesSDK(BaseLLMSDK):
    def __init__(
        self, api_key: str, base_url: str | None = None, provider_slug: str | None = None
    ):
        resolved = base_url or "https://api.openai.com/v1"
        super().__init__(api_key=api_key, base_url=resolved)
        self._client: AsyncOpenAI | None = None
        self._provider_slug = (provider_slug or "").lower() or None

    @property
    def client(self) -> AsyncOpenAI:  # pyright: ignore[reportIncompatibleMethodOverride]
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=self.api_key, base_url=self.base_url, timeout=None, max_retries=3
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None

    # ------------------------------------------------------------------
    # Request lowering (payload building)
    # ------------------------------------------------------------------

    def _resolve_effort(self, config: GenerationConfig) -> str | None:
        """Clamp the level to the model's supported set, then map
        it through ``thinking_level_map``. "off" resolves to None (omitted).
        """
        level = config.thinking_level
        if not level or level == "none":
            return None
        supported = get_supported_thinking_levels(
            reasoning=True, thinking_level_map=config.thinking_level_map
        )
        clamped = clamp_thinking_level(level, supported)
        if clamped == "off":
            return None
        mapped = (config.thinking_level_map or {}).get(clamped)
        return mapped if isinstance(mapped, str) else clamped

    def _build_payload(
        self, messages: list[Message], config: GenerationConfig, tools: list[dict] | None = None
    ) -> dict[str, Any]:
        input_items: list[dict[str, Any]] = []
        for msg in messages:
            metadata = msg.metadata or {}
            role = msg.role
            # Replay encrypted reasoning items first (stateless store:false).
            # They arrive as serialized JSON in metadata["reasoning_items"] (see
            # _stream_step / provider ThinkPart round-trip).
            if role == "assistant" and metadata.get("reasoning_items"):
                for item_json in metadata["reasoning_items"]:
                    try:
                        item = json.loads(item_json) if isinstance(item_json, str) else item_json
                    except Exception:
                        continue
                    if isinstance(item, dict) and item.get("type") == "reasoning":
                        input_items.append(item)
            tool_calls = metadata.get("tool_calls")
            if role == "assistant" and tool_calls:
                if msg.content:
                    input_items.append(
                        {
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": msg.content}],
                        }
                    )
                for tc in tool_calls:
                    # Metadata carries the OpenAI Chat shape
                    # {id, type: "function", function: {name, arguments}}.
                    fn = tc.get("function", {})
                    input_items.append(
                        {
                            "type": "function_call",
                            "call_id": tc.get("id", ""),
                            "name": fn.get("name", ""),
                            "arguments": fn.get("arguments", "{}"),
                        }
                    )
                continue
            if role == "tool":
                input_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": metadata.get("tool_call_id", ""),
                        "output": msg.content,
                    }
                )
                continue
            if role == "system":
                input_items.append({"role": "system", "content": msg.content})
                continue
            # For plain assistant messages that already replayed reasoning_items,
            # avoid double-adding empty content when the turn was reasoning-only.
            if role == "assistant" and metadata.get("reasoning_items") and not msg.content:
                continue
            input_items.append(
                {"role": "user" if role == "user" else "assistant", "content": msg.content}
            )

        model = (
            (config.model or "").strip() or os.getenv("VTX_MODEL", "").strip() or _DEFAULT_MODEL
        )
        payload: dict[str, Any] = {
            "model": model,
            "input": input_items,
            "stream": True,
            "store": False,
        }

        effort = self._resolve_effort(config)
        if effort is not None and effort != "none":
            payload["reasoning"] = {"effort": effort}
            # Include encrypted reasoning so stateless turns can replay it
            # (store:false already default; encrypted_content populated by default
            # on ZDR/store:false per docs, but include keeps compat with older APIs)
            payload["include"] = ["reasoning.encrypted_content"]

        if config.max_tokens:
            payload["max_output_tokens"] = config.max_tokens
        # Temperature rides the wire only when explicitly set (0.7 is the
        # unset default, same convention as the Chat Completions transport);
        # reasoning models reject it otherwise.
        if config.temperature is not None and config.temperature != 0.7:
            payload["temperature"] = config.temperature
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "name": t["function"]["name"],
                    "description": t["function"].get("description", ""),
                    "parameters": t["function"].get("parameters", {}),
                }
                for t in tools
            ]
            if config.tool_choice is not None:
                payload["tool_choice"] = config.tool_choice
        return payload

    # ------------------------------------------------------------------
    # Stream state machine (event dispatch)
    # ------------------------------------------------------------------

    @staticmethod
    def _new_stream_state() -> dict[str, Any]:
        return {
            "calls": {},  # item_id -> {"call_id", "name", "arguments"}
            "has_function_call": False,
            "usage": None,
            "finish_reason": None,
        }

    def _stream_step(self, state: dict[str, Any], event: dict[str, Any]) -> list[dict[str, Any]]:
        """Handle one SSE event; returns chunks to yield (may be empty)."""
        etype = event.get("type", "")
        chunks: list[dict[str, Any]] = []

        if etype in ("response.output_text.delta", "response.refusal.delta"):
            if event.get("delta"):
                chunks.append({"type": "text", "content": event["delta"]})

        elif etype in (
            "response.reasoning_text.delta",
            "response.reasoning_summary.delta",
            "response.reasoning_summary_text.delta",
        ):
            delta = event.get("delta") or event.get("text") or ""
            if delta:
                chunks.append({"type": "reasoning", "content": delta})

        elif etype == "response.output_item.added":
            item = event.get("item") or {}
            if item.get("type") == "function_call":
                item_id = item.get("id") or f"idx-{event.get('output_index', 0)}"
                state["calls"][item_id] = {
                    "call_id": item.get("call_id", ""),
                    "name": item.get("name", ""),
                    "arguments": "",
                }
                state["has_function_call"] = True

        elif etype == "response.function_call_arguments.delta":
            item_id = event.get("item_id")
            call = state["calls"].get(item_id)
            if call is not None and event.get("delta"):
                call["arguments"] += event["delta"]

        elif etype == "response.function_call_arguments.done":
            item_id = event.get("item_id")
            call = state["calls"].get(item_id)
            if call is not None and event.get("arguments") is not None:
                call["arguments"] = event["arguments"]

        elif etype == "response.reasoning_summary_part.done":
            # Separator between summary parts
            chunks.append({"type": "reasoning", "content": "\n\n"})

        elif etype == "response.output_item.done":
            item = event.get("item") or {}
            if item.get("type") == "function_call":
                item_id = item.get("id") or f"idx-{event.get('output_index', 0)}"
                call = state["calls"].setdefault(
                    item_id,
                    {
                        "call_id": item.get("call_id", ""),
                        "name": item.get("name", ""),
                        "arguments": item.get("arguments", "{}"),
                    },
                )
                if item.get("arguments") is not None:
                    call["arguments"] = item["arguments"]
                chunks.append(
                    {
                        "type": "tool_calls",
                        "tool_calls": [
                            {
                                "id": call["call_id"],
                                "name": call["name"],
                                "arguments": call["arguments"] or "{}",
                            }
                        ],
                    }
                )
            elif item.get("type") == "reasoning":
                # Preserve full reasoning item for stateless replay (encrypted_content)
                with contextlib.suppress(Exception):
                    chunks.append({"type": "reasoning_item", "item": json.dumps(item)})

        elif etype in ("response.completed", "response.incomplete"):
            resp_obj = event.get("response") or {}
            usage = resp_obj.get("usage") or {}
            usage_dict: dict[str, Any] = {
                "prompt_tokens": usage.get("input_tokens", 0),
                "completion_tokens": usage.get("output_tokens", 0),
                "total_tokens": usage.get(
                    "total_tokens", usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
                ),
            }
            # Surface cached/reasoning token details
            itd = usage.get("input_tokens_details") or {}
            otd = usage.get("output_tokens_details") or {}
            if itd.get("cached_tokens"):
                usage_dict["cached_tokens"] = itd["cached_tokens"]
            if otd.get("reasoning_tokens"):
                usage_dict["reasoning_tokens"] = otd["reasoning_tokens"]
            state["usage"] = usage_dict
            status = resp_obj.get("status", "completed")
            incomplete_reason = (
                resp_obj.get("incomplete_details", {}).get("reason")
                if status == "incomplete"
                else None
            )
            if status == "incomplete":
                # Only max_output_tokens maps to length; everything else is error
                if incomplete_reason == "max_output_tokens":
                    state["finish_reason"] = "length"
                else:
                    state["finish_reason"] = "error"
                    state["error"] = incomplete_reason or "incomplete"
            else:
                state["finish_reason"] = "tool_calls" if state["has_function_call"] else "stop"
            # Terminal chunks are emitted in-step.
            if state["usage"]:
                chunks.append({"type": "usage", "usage": state["usage"]})
            chunks.append({"type": "finish_reason", "finish_reason": state["finish_reason"]})

        elif etype == "response.failed":
            err = (event.get("response") or {}).get("error") or {}
            raise RuntimeError(f"OpenAI Responses failed: {err.get('message', 'unknown error')}")

        elif etype == "error":
            # Official SDK's ResponseErrorEvent carries a top-level message.
            err = event.get("error") or {}
            msg = event.get("message") or err.get("message", "unknown")
            raise RuntimeError(f"OpenAI Responses stream error: {msg}")

        return chunks

    # ------------------------------------------------------------------
    # BaseLLMSDK interface
    # ------------------------------------------------------------------

    async def generate(
        self, messages: list[Message], config: GenerationConfig, stream: bool = False
    ) -> GenerationResponse | AsyncGenerator:
        if stream:
            return self._generate_stream(messages, config, tools=None)
        return await self._generate_blocking(messages, config, tools=None)

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

    async def _generate_blocking(
        self, messages: list[Message], config: GenerationConfig, tools: list[dict] | None
    ) -> GenerationResponse:
        payload = self._build_payload(messages, config, tools)
        payload.pop("stream", None)
        response = await self.client.responses.create(**payload)
        data = response.model_dump()

        content = ""
        reasoning = ""
        tool_calls: list[ToolCall] = []
        for item in data.get("output", []):
            item_type = item.get("type")
            if item_type == "message":
                for part in item.get("content", []):
                    if part.get("type") == "output_text":
                        content += part.get("text", "")
            elif item_type == "reasoning":
                for part in item.get("summary", []):
                    if part.get("type") == "summary_text":
                        reasoning += part.get("text", "")
            elif item_type == "function_call":
                tool_calls.append(
                    ToolCall(
                        id=item.get("call_id", ""),
                        name=item.get("name", ""),
                        arguments=item.get("arguments", "{}"),
                    )
                )

        usage = data.get("usage") or {}
        return GenerationResponse(
            content=content,
            model=data.get("model", payload["model"]),
            finish_reason="tool_calls" if tool_calls else "stop",
            usage={
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
                "total_tokens": usage.get(
                    "total_tokens", usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
                ),
            },
            reasoning_content=reasoning,
        )

    def _generate_stream(  # type: ignore[override]
        self, messages: list[Message], config: GenerationConfig, tools: list[dict] | None = None
    ) -> AsyncGenerator[dict[str, Any], None]:
        return self._stream_response(self._build_payload(messages, config, tools))

    async def _stream_response(
        self, payload: dict[str, Any]
    ) -> AsyncGenerator[dict[str, Any], None]:
        state = self._new_stream_state()
        finish_emitted = False
        usage_emitted = False
        try:
            # Official SDK flow: create() with stream=True returns an
            # AsyncStream of typed ResponseStreamEvent models.
            stream = await self.client.responses.create(**payload)
            async for event in stream:
                for chunk in self._stream_step(state, event.model_dump()):
                    if chunk["type"] == "finish_reason":
                        finish_emitted = True
                    elif chunk["type"] == "usage":
                        usage_emitted = True
                    yield chunk

            # Terminal flush — only for streams that
            # never sent a terminal event.
            if state["usage"] and not usage_emitted:
                yield {"type": "usage", "usage": state["usage"]}
            if not finish_emitted:
                reason = "tool_calls" if state["has_function_call"] else "stop"
                yield {"type": "finish_reason", "finish_reason": reason}
        except (GeneratorExit, asyncio.CancelledError):
            return

    def get_available_models(self) -> list[str]:
        return []

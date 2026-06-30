"""
Bridge module: converts between claw's dict-based message format and
vtx's typed Pydantic message types.

All claw modules that want to use vtx internals go through this module
so consumers are isolated from the conversion logic.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from vtx.core.types import (
    AssistantMessage,
    ImageContent,
    Message,
    StopReason,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)

# ═══════════════════════════════════════════════════════════════════════
# Dict ↔ vtx Message conversion
# ═══════════════════════════════════════════════════════════════════════


def dict_to_vtx_message(msg: dict[str, Any]) -> Message | None:
    """Convert an OpenAI-style dict message to a vtx typed Message."""
    role = msg.get("role", "")
    if role == "user":
        return _user_to_vtx(msg)
    if role == "assistant":
        return _assistant_to_vtx(msg)
    if role == "tool":
        return _tool_result_to_vtx(msg)
    return None  # system role, unknown role, etc.


def _user_to_vtx(msg: dict[str, Any]) -> UserMessage:
    content = msg.get("content", "")
    if isinstance(content, str):
        return UserMessage(content=content)
    if isinstance(content, list):
        text_parts: list[str] = []
        images: list[dict[str, str]] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    text_parts.append(str(block.get("text", "")))
                elif block.get("type") == "image_url":
                    url = str(block.get("image_url", {}).get("url", ""))
                    if url.startswith("data:image/"):
                        mime, _, b64 = url[5:].partition(";")
                        images.append({"data": b64.removeprefix("base64,"), "mime_type": mime})
        if images:
            result_content: list[TextContent | ImageContent] = []
            if text_parts:
                result_content.append(TextContent(text="\n".join(text_parts)))
            for img in images:
                result_content.append(ImageContent(data=img["data"], mime_type=img["mime_type"]))
            return UserMessage(content=result_content)
        return UserMessage(content="\n".join(text_parts))
    return UserMessage(content="")


def _assistant_to_vtx(msg: dict[str, Any]) -> AssistantMessage:
    content_str = msg.get("content") or ""
    content_blocks: list[TextContent | ThinkingContent | ToolCall] = []
    if isinstance(content_str, str) and content_str.strip():
        content_blocks.append(TextContent(text=content_str))
    elif isinstance(content_str, list):
        for block in content_str:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    content_blocks.append(TextContent(text=str(block.get("text", ""))))
                elif block.get("type") == "thinking":
                    content_blocks.append(
                        ThinkingContent(
                            thinking=str(block.get("thinking", "")),
                            signature=block.get("signature"),
                        )
                    )

    # Tool calls from OpenAI-style tool_calls array
    for tc in msg.get("tool_calls") or []:
        if isinstance(tc, dict):
            fn = tc.get("function") or {}
            name = fn.get("name") or tc.get("name") or ""
            if name:
                raw_args = fn.get("arguments") or "{}"
                try:
                    arguments = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except (json.JSONDecodeError, TypeError):
                    arguments = {}
                content_blocks.append(
                    ToolCall(
                        id=str(tc.get("id", "")),
                        name=name,
                        arguments=arguments,
                    )
                )

    # Reasoning content — prefer structured thinking_blocks over flat reasoning_content
    tb_list = msg.get("thinking_blocks") or []
    if tb_list:
        for tb in tb_list:
            if isinstance(tb, dict):
                content_blocks.append(
                    ThinkingContent(
                        thinking=str(tb.get("thinking", "")),
                        signature=tb.get("signature"),
                    )
                )
    else:
        reasoning = msg.get("reasoning_content") or ""
        if reasoning:
            content_blocks.append(ThinkingContent(thinking=reasoning))

    usage_raw = msg.get("usage")
    usage = None
    if isinstance(usage_raw, dict):
        usage = Usage(
            input_tokens=usage_raw.get("prompt_tokens", 0) or usage_raw.get("input_tokens", 0),
            output_tokens=usage_raw.get("completion_tokens", 0)
            or usage_raw.get("output_tokens", 0),
            cache_read_tokens=usage_raw.get("cache_read_tokens", 0),
            cache_write_tokens=usage_raw.get("cache_write_tokens", 0),
        )

    return AssistantMessage(
        content=content_blocks,
        usage=usage,
        stop_reason=claw_verdict_to_vtx(msg.get("finish_reason", msg.get("stop_reason", ""))),
    )


def _tool_result_to_vtx(msg: dict[str, Any]) -> ToolResultMessage:
    content = msg.get("content", "")
    content_blocks: list[TextContent | ImageContent] = []
    if isinstance(content, str):
        if content:
            content_blocks.append(TextContent(text=content))
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    content_blocks.append(TextContent(text=str(block.get("text", ""))))
                elif block.get("type") == "image_url":
                    url = str(block.get("image_url", {}).get("url", ""))
                    if url.startswith("data:image/"):
                        mime, _, b64 = url[5:].partition(";")
                        content_blocks.append(
                            ImageContent(data=b64.removeprefix("base64,"), mime_type=mime)
                        )

    return ToolResultMessage(
        tool_call_id=str(msg.get("tool_call_id", "")),
        tool_name=str(msg.get("name", "tool")),
        content=content_blocks or [TextContent(text="(no output)")],
    )


def vtx_message_to_dict(msg: Message) -> dict[str, Any]:
    """Convert a vtx typed Message back to an OpenAI-style dict."""
    if isinstance(msg, UserMessage):
        if isinstance(msg.content, str):
            return {"role": "user", "content": msg.content}
        # List of content blocks
        text_parts: list[str] = []
        image_blocks: list[dict] = []
        for block in msg.content:
            if isinstance(block, TextContent):
                text_parts.append(block.text)
            elif isinstance(block, ImageContent):
                b64 = block.data
                if not b64.startswith("base64,"):
                    b64 = f"base64,{b64}"
                image_blocks.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{block.mime_type};{b64}"},
                    }
                )
        result_content: list[dict] = []
        if text_parts:
            result_content.append({"type": "text", "text": "\n".join(text_parts)})
        result_content.extend(image_blocks)
        return {"role": "user", "content": result_content}

    if isinstance(msg, AssistantMessage):
        text = ""
        thinking_parts: list[str] = []
        thinking_blocks: list[dict] = []
        tool_calls: list[dict] = []
        for block in msg.content:
            if isinstance(block, TextContent):
                text += block.text
            elif isinstance(block, ThinkingContent):
                thinking_parts.append(block.thinking)
                tb: dict[str, Any] = {"type": "thinking", "thinking": block.thinking}
                if block.signature:
                    tb["signature"] = block.signature
                thinking_blocks.append(tb)
            elif isinstance(block, ToolCall):
                tool_calls.append(
                    {
                        "id": block.id,
                        "type": "function",
                        "function": {
                            "name": block.name,
                            "arguments": json.dumps(block.arguments),
                        },
                    }
                )
        result: dict[str, Any] = {"role": "assistant", "content": text}
        if tool_calls:
            result["tool_calls"] = tool_calls
        if thinking_parts:
            result["reasoning_content"] = "\n".join(thinking_parts)
        if thinking_blocks:
            result["thinking_blocks"] = thinking_blocks
        if msg.usage:
            result["usage"] = _vtx_usage_to_claw(msg.usage)
        return result

    if isinstance(msg, ToolResultMessage):
        text = "".join(block.text for block in msg.content if isinstance(block, TextContent))
        return {
            "role": "tool",
            "tool_call_id": msg.tool_call_id,
            "name": msg.tool_name,
            "content": text,
        }

    return {"role": "unknown", "content": ""}


def dicts_to_vtx_messages(messages: list[dict[str, Any]]) -> list[Message]:
    """Convert a list of OpenAI-style dict messages to vtx typed Messages."""
    result: list[Message] = []
    for msg in messages:
        converted = dict_to_vtx_message(msg)
        if converted is not None:
            result.append(converted)
    return result


def vtx_to_dict_messages(messages: list[Message]) -> list[dict[str, Any]]:
    """Convert a list of vtx typed Messages to OpenAI-style dict messages."""
    return [vtx_message_to_dict(m) for m in messages]


# ═══════════════════════════════════════════════════════════════════════
# StopReason conversion
# ═══════════════════════════════════════════════════════════════════════

_STOP_REASON_MAP: dict[str, StopReason] = {
    "stop": StopReason.STOP,
    "length": StopReason.LENGTH,
    "tool_calls": StopReason.TOOL_USE,
    "function_call": StopReason.TOOL_USE,
    "tool_use": StopReason.TOOL_USE,
    "error": StopReason.ERROR,
    "interrupted": StopReason.INTERRUPTED,
    "max_iterations": StopReason.LENGTH,
    "cancelled": StopReason.INTERRUPTED,
    "tool_error": StopReason.ERROR,
    "empty_final_response": StopReason.STOP,
    "completed": StopReason.STOP,
}

_REVERSE_STOP_REASON: dict[StopReason, str] = {
    StopReason.STOP: "stop",
    StopReason.LENGTH: "length",
    StopReason.TOOL_USE: "tool_calls",
    StopReason.ERROR: "error",
    StopReason.INTERRUPTED: "interrupted",
    StopReason.STEER: "stop",
}


def claw_verdict_to_vtx(verdict: str | None) -> StopReason:
    """Convert a string-based stop reason verdict to vtx StopReason enum."""
    if not verdict:
        return StopReason.STOP
    return _STOP_REASON_MAP.get(verdict, StopReason.STOP)


def vtx_stop_reason_to_claw(reason: StopReason) -> str:
    return _REVERSE_STOP_REASON.get(reason, "stop")


# ═══════════════════════════════════════════════════════════════════════
# Usage conversion
# ═══════════════════════════════════════════════════════════════════════


def _vtx_usage_to_claw(usage: Usage) -> dict[str, int]:
    return {
        "prompt_tokens": max(0, usage.input_tokens),
        "completion_tokens": max(0, usage.output_tokens),
        "total_tokens": max(0, usage.total_tokens),
        "cache_read_tokens": max(0, usage.cache_read_tokens),
        "cache_write_tokens": max(0, usage.cache_write_tokens),
    }


def claw_usage_to_vtx(usage: dict[str, int] | None) -> Usage:
    if not usage:
        return Usage()
    return Usage(
        input_tokens=usage.get("prompt_tokens", 0) or usage.get("input_tokens", 0),
        output_tokens=usage.get("completion_tokens", 0) or usage.get("output_tokens", 0),
        cache_read_tokens=usage.get("cache_read_tokens", 0),
        cache_write_tokens=usage.get("cache_write_tokens", 0),
    )


# ═══════════════════════════════════════════════════════════════════════
# Provider adapter: wraps claw's LLMProvider as a vtx-compatible provider
# ═══════════════════════════════════════════════════════════════════════


class _ClawProviderAdapter:
    """Wraps claw's LLMProvider to implement vtx's streaming provider interface.

    Converts callback-based streaming into vtx's async-iterator-based model.
    The ``stream()`` method returns an ``LLMStream`` suitable for use with
    ``vtx.turn.run_single_turn()``.

    The adapter also exposes ``last_usage`` (dict) and ``last_response`` for
    consuming code to inspect the final provider response after iteration.
    """

    name = "claw_adapter"

    def __init__(
        self,
        claw_provider: Any,
        *,
        model: str,
        provider_retry_mode: str = "standard",
        on_retry_wait: Any = None,
    ):
        self._claw = claw_provider
        self._model = model
        self._provider_retry_mode = provider_retry_mode
        self._on_retry_wait = on_retry_wait
        self._last_usage: dict[str, int] | None = None
        self._last_response: Any = None
        self._last_finish_reason: str = "stop"

    @property
    def last_usage(self) -> dict[str, int]:
        return self._last_usage or {}

    @property
    def last_response(self) -> Any:
        return self._last_response

    async def stream(
        self,
        messages: list[Message],
        *,
        system_prompt: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
        on_stream_recover: Any = None,
    ) -> Any:
        """Return an LLMStream that adapts claw's streaming to vtx's model.

        The stream is lazy — the actual API call happens when iteration begins.
        """
        from vtx.llm.base import LLMStream

        llm_stream = LLMStream()

        async def _stream_iter():
            async for part in self._do_stream(
                messages,
                system_prompt=system_prompt,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
                reasoning_effort=reasoning_effort,
                on_stream_recover=on_stream_recover,
            ):
                yield part

        llm_stream.set_iterator(_stream_iter())
        return llm_stream

    async def _do_stream(
        self,
        messages: list[Message],
        *,
        system_prompt: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
        on_stream_recover: Any = None,
    ):
        """Core streaming logic: converts claw's callbacks to vtx stream parts."""
        from vtx.core.types import (
            StreamDone,
            TextPart,
            ThinkPart,
            ToolCallDelta,
            ToolCallStart,
        )

        dict_messages = vtx_to_dict_messages(messages)
        kwargs: dict[str, Any] = {
            "messages": dict_messages,
            "model": self._model,
            "retry_mode": self._provider_retry_mode,
            "on_retry_wait": self._on_retry_wait,
        }
        if tools is not None:
            kwargs["tools"] = tools
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if reasoning_effort is not None:
            kwargs["reasoning_effort"] = reasoning_effort

        has_streaming = (
            "chat_stream_with_retry" in type(self._claw).__dict__
            or "chat_stream_with_retry" in self._claw.__dict__
        )

        if not has_streaming:
            # Non-streaming fallback
            response = await self._claw.chat_with_retry(**kwargs)
            self._last_response = response
            content = getattr(response, "content", None)
            if content:
                yield TextPart(text=content)
            reasoning = getattr(response, "reasoning_content", None)
            if reasoning:
                yield ThinkPart(think=reasoning)
            for i, tc in enumerate(getattr(response, "tool_calls", None) or []):
                tc_id = getattr(tc, "id", f"call_{i}")
                tc_name = getattr(tc, "name", "")
                tc_args = getattr(tc, "arguments", {})
                yield ToolCallStart(id=tc_id, name=tc_name, index=i, arguments=tc_args)
                if isinstance(tc_args, dict) and tc_args:
                    yield ToolCallDelta(index=i, arguments_delta=json.dumps(tc_args))
                elif isinstance(tc_args, str) and tc_args.strip():
                    yield ToolCallDelta(index=i, arguments_delta=tc_args)
            finish_reason = getattr(response, "finish_reason", None) or "stop"
            self._last_usage = _usage_dict_from_response(response)
            self._last_finish_reason = finish_reason
            yield StreamDone(stop_reason=claw_verdict_to_vtx(finish_reason))
            return

        # Streaming path: queue-based callback adapter
        queue: asyncio.Queue = asyncio.Queue()
        error_ref: list[Exception] = []
        response_ref: list[Any] = []
        _yielded_text: bool = False
        _yielded_tool_call: bool = False

        async def on_content_delta(delta: str) -> None:
            nonlocal _yielded_text
            _yielded_text = bool(delta) or _yielded_text
            await queue.put(("text", _sanitize_surrogates(delta)))

        async def on_thinking_delta(delta: str) -> None:
            await queue.put(("think", delta))

        async def on_tool_call_delta(delta: dict[str, Any]) -> None:
            nonlocal _yielded_tool_call
            _yielded_tool_call = True
            await queue.put(("tool_call", delta))

        _recover_callback = on_stream_recover or (lambda: None)

        async def _on_stream_recover() -> None:
            if callable(_recover_callback):
                r = _recover_callback()
                if r is not None and hasattr(r, "__await__"):
                    await r

        async def _run_stream():
            try:
                resp = await self._claw.chat_stream_with_retry(
                    **kwargs,
                    on_content_delta=on_content_delta,
                    on_thinking_delta=on_thinking_delta,
                    on_tool_call_delta=on_tool_call_delta,
                    on_stream_recover=_on_stream_recover,
                )
                response_ref.append(resp)
            except Exception as e:
                error_ref.append(e)
            finally:
                await queue.put(("_done", None))

        stream_task = asyncio.create_task(_run_stream())

        try:
            while True:
                event_type, data = await queue.get()
                if event_type == "_done":
                    break
                if event_type == "text":
                    yield TextPart(text=data)
                elif event_type == "think":
                    yield ThinkPart(think=data)
                elif event_type == "tool_call":
                    tc_id = data.get("id", "")
                    tc_name = data.get("name", "")
                    tc_args = data.get("arguments", "{}")
                    fn = data.get("function", {})
                    if not tc_name:
                        tc_name = fn.get("name", "")
                    if not tc_id:
                        tc_id = fn.get("id", "")
                    if not tc_id:
                        tc_id = "call_" + str(hash(str(data)) & 0xFFFFFFFF)
                    if not tc_args:
                        tc_args = fn.get("arguments", "{}")

                    yield ToolCallStart(id=tc_id, name=tc_name, index=0, arguments=tc_args)
                    if isinstance(tc_args, str) and tc_args.strip():
                        yield ToolCallDelta(index=0, arguments_delta=tc_args)
        finally:
            if not stream_task.done():
                stream_task.cancel()
                try:
                    await stream_task
                except (asyncio.CancelledError, Exception):
                    pass

        if error_ref:
            raise error_ref[0]

        final_response = response_ref[0] if response_ref else None
        if final_response is not None:
            # Fallback: if the provider returned data through the response
            # object instead of streaming callbacks, yield it now.
            if not _yielded_text and not _yielded_tool_call:
                content = getattr(final_response, "content", None)
                if content:
                    yield TextPart(text=content)
                for i, tc in enumerate(getattr(final_response, "tool_calls", None) or []):
                    tc_id = getattr(tc, "id", f"call_{i}")
                    tc_name = getattr(tc, "name", "")
                    tc_args = getattr(tc, "arguments", {})
                    yield ToolCallStart(id=tc_id, name=tc_name, index=i, arguments=tc_args)
                    if isinstance(tc_args, dict) and tc_args:
                        yield ToolCallDelta(index=i, arguments_delta=json.dumps(tc_args))
                    elif isinstance(tc_args, str) and tc_args.strip():
                        yield ToolCallDelta(index=i, arguments_delta=tc_args)

            self._last_response = final_response
            self._last_usage = _usage_dict_from_response(final_response)
            finish_reason = getattr(final_response, "finish_reason", None) or "stop"
            self._last_finish_reason = finish_reason
            yield StreamDone(stop_reason=claw_verdict_to_vtx(finish_reason))

    def should_retry_for_error(self, error: Exception) -> bool:
        if hasattr(self._claw, "should_retry_for_error"):
            return self._claw.should_retry_for_error(error)
        return False


def _usage_dict_from_response(response: Any) -> dict[str, int]:
    """Extract a usage dict from a claw LLMResponse or similar object."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return {}
    if isinstance(usage, dict):
        return {k: int(v or 0) for k, v in usage.items() if not isinstance(v, dict)}
    if hasattr(usage, "model_dump"):
        return {
            k: int(v or 0) for k, v in usage.model_dump().items() if isinstance(v, (int, float))
        }
    return {}


def _sanitize_surrogates(text: str) -> str:
    """Remove surrogate characters that some providers may emit."""
    return text.encode("utf-8", errors="replace").decode("utf-8")


# ═══════════════════════════════════════════════════════════════════════
# Lightweight LLM streaming via bridge adapter (no tool execution)
# ═══════════════════════════════════════════════════════════════════════


async def create_bridge_stream(
    provider: Any,
    messages: list[dict[str, Any]],
    model: str,
    *,
    tool_defs: list[dict[str, Any]] | None = None,
    provider_retry_mode: str = "standard",
    on_retry_wait: Any = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    reasoning_effort: str | None = None,
    system_prompt: str | None = None,
    on_stream_recover: Any = None,
) -> tuple[Any, Any]:
    """Create a bridge provider adapter and LLM stream for a single LLM call.

    Returns ``(adapter, stream)`` where *adapter* is a ``_ClawProviderAdapter``
    and *stream* is an ``LLMStream`` (async iterable of vtx stream parts).

    After iterating over the stream, callers can inspect ``adapter.last_usage``
    (dict) and ``adapter.last_response`` (the claw ``LLMResponse``).

    This is a lightweight alternative to ``run_vtx_turn()`` that does NOT
    execute tools — it only makes the LLM call via the provider adapter.
    """
    adapter = _ClawProviderAdapter(
        provider,
        model=model,
        provider_retry_mode=provider_retry_mode,
        on_retry_wait=on_retry_wait,
    )

    vtx_messages = dicts_to_vtx_messages(messages)
    stream = await adapter.stream(
        vtx_messages,
        system_prompt=system_prompt,
        tools=tool_defs,
        temperature=temperature,
        max_tokens=max_tokens,
        reasoning_effort=reasoning_effort,
        on_stream_recover=on_stream_recover,
    )

    return adapter, stream

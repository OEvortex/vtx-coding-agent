"""OpenAI GPT SDK using the official openai package."""

import asyncio
import logging
import os
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionChunk, ChatCompletionToolParam

from vtx.ai.provider_hooks import prepare_request
from vtx.ai.sdk.base import BaseLLMSDK, GenerationConfig, GenerationResponse, Message, ToolCall
from vtx.ai.thinking import OPENAI_COMPLETIONS, resolve_reasoning_params

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "gpt-4o"
_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 1.0


def _is_transient_error(e: Exception) -> bool:
    msg = str(e).lower()
    return any(
        s in msg
        for s in [
            "connection",
            "connect",
            "timeout",
            "timed out",
            "reset",
            "broken pipe",
            "eof",
            "network",
            "unavailable",
            "bad gateway",
            "gateway timeout",
            "service unavailable",
            "provider returned error",
            "overloaded",
            "capacity",
        ]
    )


async def _retry_on_transient(coro_factory, max_retries: int = _MAX_RETRIES) -> Any:
    last_error = None
    for attempt in range(max_retries):
        try:
            return await coro_factory()
        except Exception as e:
            last_error = e
            if not _is_transient_error(e) or attempt == max_retries - 1:
                raise
            delay = _RETRY_BASE_DELAY * (2**attempt)
            logger.warning(
                "Transient error (attempt %d/%d), retrying in %.1fs: %s",
                attempt + 1,
                max_retries,
                delay,
                str(e)[:200],
            )
            await asyncio.sleep(delay)
    if last_error is not None:
        raise last_error


def _normalize_usage(usage: Any) -> dict[str, Any]:
    """Flatten OpenAI chat/completions usage into the SDK's canonical token names.

    ``model_dump()`` keeps cache and reasoning counts nested under
    ``prompt_tokens_details``/``completion_tokens_details``, but every consumer
    (``openai_sdk._process_stream``, ``tui/export``) reads flat keys.
    """
    try:
        data = usage.model_dump()
    except Exception:
        data = dict(usage) if isinstance(usage, dict) else {}
    prompt = data.get("prompt_tokens", 0) or data.get("input_tokens", 0)
    completion = data.get("completion_tokens", 0) or data.get("output_tokens", 0)
    out: dict[str, Any] = {
        "input_tokens": prompt,
        "output_tokens": completion,
        "total_tokens": data.get("total_tokens", prompt + completion),
    }
    ptd = data.get("prompt_tokens_details") or {}
    if ptd.get("cached_tokens"):
        out["cached_tokens"] = ptd["cached_tokens"]
    if ptd.get("cache_write_tokens"):
        out["cache_write_tokens"] = ptd["cache_write_tokens"]
    ctd = data.get("completion_tokens_details") or {}
    if ctd.get("reasoning_tokens"):
        out["reasoning_tokens"] = ctd["reasoning_tokens"]
    return out


async def _openai_stream_chunks(
    stream: AsyncIterator[ChatCompletionChunk],
) -> AsyncGenerator[dict[str, Any], None]:
    from vtx.ai.phase_parser import (
        INLINE_THINK_SIGNATURE,
        ResponseDelta,
        ResponseEnd,
        ResponseStart,
        ThinkDelta,
        ThinkEnd,
        ThinkingPhaseParser,
        ThinkStart,
    )

    tool_calls_acc: dict[int, dict[str, Any]] = {}
    phase_parser = ThinkingPhaseParser()
    think_emitted_len = 0
    finish_reason: str | None = None
    try:
        async for chunk in stream:
            if chunk.usage:
                yield {"type": "usage", "usage": _normalize_usage(chunk.usage)}
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            if choice.finish_reason:
                finish_reason = choice.finish_reason
            delta = choice.delta
            reasoning_delta = (
                getattr(delta, "reasoning_content", None)
                or getattr(delta, "reasoning", None)
                or getattr(delta, "reasoning_text", None)
            )
            if reasoning_delta:
                yield {
                    "type": "reasoning",
                    "content": reasoning_delta,
                    "signature": "reasoning_content",
                }
            elif delta.content:
                for phase_event in phase_parser.feed(delta.content):
                    if isinstance(phase_event, ThinkStart):
                        pass
                    elif isinstance(phase_event, ThinkDelta):
                        think_emitted_len += len(phase_event.text)
                        yield {
                            "type": "reasoning",
                            "content": phase_event.text,
                            "signature": INLINE_THINK_SIGNATURE,
                        }
                    elif isinstance(phase_event, ThinkEnd):
                        remaining = phase_event.full_thinking[think_emitted_len:]
                        if remaining:
                            yield {
                                "type": "reasoning",
                                "content": remaining,
                                "signature": INLINE_THINK_SIGNATURE,
                            }
                        think_emitted_len = 0
                    elif isinstance(phase_event, ResponseStart):
                        pass
                    elif isinstance(phase_event, ResponseDelta):
                        yield {"type": "text", "content": phase_event.text}
                    elif isinstance(phase_event, ResponseEnd):
                        pass
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in tool_calls_acc:
                        tool_calls_acc[idx] = {"id": "", "name": "", "arguments": ""}
                    if tc_delta.id:
                        tool_calls_acc[idx]["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            tool_calls_acc[idx]["name"] = tc_delta.function.name
                        if tc_delta.function.arguments:
                            tool_calls_acc[idx]["arguments"] += tc_delta.function.arguments
        for phase_event in phase_parser.flush():
            if isinstance(phase_event, ThinkDelta):
                think_emitted_len += len(phase_event.text)
                yield {
                    "type": "reasoning",
                    "content": phase_event.text,
                    "signature": INLINE_THINK_SIGNATURE,
                }
            elif isinstance(phase_event, ThinkEnd):
                remaining = phase_event.full_thinking[think_emitted_len:]
                if remaining:
                    yield {
                        "type": "reasoning",
                        "content": remaining,
                        "signature": INLINE_THINK_SIGNATURE,
                    }
                think_emitted_len = 0
            elif isinstance(phase_event, ResponseDelta):
                yield {"type": "text", "content": phase_event.text}
        if tool_calls_acc:
            yield {
                "type": "tool_calls",
                "tool_calls": [
                    ToolCall(id=v["id"], name=v["name"], arguments=v["arguments"])
                    for v in sorted(tool_calls_acc.values(), key=lambda x: x["id"])
                ],
            }
        if finish_reason:
            yield {"type": "finish_reason", "finish_reason": finish_reason}
    except (GeneratorExit, asyncio.CancelledError):
        return
    finally:
        if hasattr(stream, "close"):
            try:
                from typing import cast as typing_cast

                await typing_cast(Any, stream).close()
            except BaseException:
                pass


def _tool_param(tool: dict[str, Any]) -> Any:
    """One wire tool entry, preserving a grammar when the provider sent one.

    ``ChatCompletionToolParam`` has no grammar field, and OpenAI's
    grammar-constrained sampling extends the function object, so a grammar is
    emitted as a plain dict. A tool without one still goes through the typed
    constructor, keeping the existing shape and validation for the common case.
    """
    function = tool.get("function", {})
    grammar = function.get("grammar")
    if grammar is None:
        return ChatCompletionToolParam(
            type="function",
            function={
                "name": function["name"],
                "description": function.get("description", ""),
                "parameters": function["parameters"],
            },
        )
    return {
        "type": "function",
        "function": {
            "name": function["name"],
            "description": function.get("description", ""),
            "parameters": function["parameters"],
            "grammar": grammar,
        },
    }


class OpenAISDK(BaseLLMSDK):
    def __init__(
        self,
        api_key: str,
        base_url: str | None = None,
        rate_limit_hook=None,
        provider_slug: str | None = None,
        default_headers: dict[str, str] | None = None,
    ):
        resolved_url = base_url or "https://api.openai.com/v1"
        if resolved_url.startswith("http://"):
            resolved_url = "https://" + resolved_url[7:]
        super().__init__(api_key, resolved_url)
        self._async_client: AsyncOpenAI | None = None
        self._rate_limit_hook = rate_limit_hook
        self._provider_slug = (provider_slug or "").lower() or None
        self._default_headers = default_headers

    @property
    def client(self) -> AsyncOpenAI:  # pyright: ignore[reportIncompatibleMethodOverride]
        if self._async_client is None:
            kwargs: dict[str, Any] = {
                "api_key": self.api_key,
                "base_url": self.base_url,
                "timeout": None,
                "max_retries": 3,
            }
            if self._default_headers:
                kwargs["default_headers"] = self._default_headers
            self._async_client = AsyncOpenAI(**kwargs)
        return self._async_client

    def _build_kwargs(
        self, messages: list[Message], config: GenerationConfig, tools: list[dict] | None = None
    ) -> dict[str, Any]:
        openai_messages = self.convert_messages_to_dict(messages)
        model = (
            config.model.strip()
            if config.model and config.model.strip()
            else os.getenv("VTX_MODEL", "").strip() or _DEFAULT_MODEL
        )
        kwargs: dict[str, Any] = {"model": model, "messages": openai_messages}
        if config.temperature is not None and config.temperature != 0.7:
            kwargs["temperature"] = config.temperature
        if config.max_tokens is not None:
            kwargs["max_completion_tokens"] = config.max_tokens
        if config.top_p is not None:
            kwargs["top_p"] = config.top_p
        if config.frequency_penalty is not None and config.frequency_penalty != 0.0:
            kwargs["frequency_penalty"] = config.frequency_penalty
        if config.presence_penalty is not None and config.presence_penalty != 0.0:
            kwargs["presence_penalty"] = config.presence_penalty
        if config.stop_sequences:
            kwargs["stop"] = config.stop_sequences
        if tools:
            kwargs["tools"] = [_tool_param(t) for t in tools]
            if config.tool_choice is not None:
                kwargs["tool_choice"] = config.tool_choice
        self._apply_thinking_kwargs(kwargs, config)
        return kwargs

    def _apply_thinking_kwargs(self, kwargs: dict[str, Any], config: GenerationConfig) -> None:
        """Translate ``config.thinking_level`` into Chat Completions wire
        params via the shared resolver (:mod:`vtx.ai.thinking`).

        Every ``openai_compat`` provider in the catalog speaks the same
        Chat Completions wire, so ``reasoning_effort`` is a standard field
        they all understand — gating it on a slug allow-list left the level
        silently unsent on every gateway, which is the same as the level not
        working at all. The resolver already drops anything the transport
        cannot express (explicitly unsupported levels, Anthropic-only token
        budgets), so a level is sent only when it is actually sendable.

        Note we send *only* the standard ``reasoning_effort`` field: an
        earlier attempt used ``extra_body={'thinking': ...}``, a non-standard
        shape that some gateways accepted while the model ignored it.
        """
        kwargs.update(
            resolve_reasoning_params(
                OPENAI_COMPLETIONS, config.thinking_level, level_map=config.thinking_level_map
            )
        )

    async def generate(
        self, messages: list[Message], config: GenerationConfig, stream: bool = False
    ) -> GenerationResponse | AsyncGenerator:
        try:
            kwargs = self._build_kwargs(messages, config)
            kwargs["stream"] = stream
            if stream:
                kwargs["stream_options"] = {"include_usage": True}
            extra_headers, kwargs = await prepare_request(
                provider=self._provider_slug or "openai", payload=kwargs
            )
            if extra_headers:
                kwargs["extra_headers"] = extra_headers
            if stream:
                raw_stream = await _retry_on_transient(
                    lambda: self.client.chat.completions.create(**kwargs)
                )
                assert raw_stream is not None
                return _openai_stream_chunks(raw_stream)
            else:

                async def _do_generate():
                    return await self.client.chat.completions.create(**kwargs)

                completion = await _retry_on_transient(_do_generate)
                assert completion is not None
                choice = completion.choices[0]
                msg = choice.message
                content = msg.content or ""
                reasoning = (
                    getattr(msg, "reasoning_content", None)
                    or getattr(msg, "reasoning", None)
                    or getattr(msg, "reasoning_text", None)
                    or ""
                )
                usage = completion.usage
                usage_dict: dict[str, Any] | None = _normalize_usage(usage) if usage else None
                return GenerationResponse(
                    content=content,
                    model=completion.model,
                    finish_reason=choice.finish_reason,
                    usage=usage_dict,
                    reasoning_content=reasoning,
                )
        except Exception as e:
            error_msg = str(e).lower()
            if "rate limit" in error_msg or "too many requests" in error_msg or "429" in error_msg:
                raise RuntimeError(f"Rate limit exceeded: {e!s}") from e
            raise RuntimeError(f"OpenAI generation failed: {e!s}") from e

    async def generate_with_tools(
        self,
        messages: list[Message],
        tools: list[dict],
        config: GenerationConfig,
        stream: bool = False,
    ) -> GenerationResponse | AsyncGenerator:
        try:
            kwargs = self._build_kwargs(messages, config, tools)
            kwargs["stream"] = stream
            if stream:
                kwargs["stream_options"] = {"include_usage": True}
            extra_headers, kwargs = await prepare_request(
                provider=self._provider_slug or "openai", payload=kwargs
            )
            if extra_headers:
                kwargs["extra_headers"] = extra_headers
            if stream:
                raw_stream = await _retry_on_transient(
                    lambda: self.client.chat.completions.create(**kwargs)
                )
                assert raw_stream is not None
                return _openai_stream_chunks(raw_stream)
            else:

                async def _do_generate():
                    return await self.client.chat.completions.create(**kwargs)

                completion = await _retry_on_transient(_do_generate)
                assert completion is not None
                choice = completion.choices[0]
                msg = choice.message
                content = msg.content or ""
                reasoning = (
                    getattr(msg, "reasoning_content", None)
                    or getattr(msg, "reasoning", None)
                    or getattr(msg, "reasoning_text", None)
                    or ""
                )
                tool_calls = []
                if msg.tool_calls:
                    for tc in msg.tool_calls:
                        tool_calls.append(
                            ToolCall(
                                id=tc.id, name=tc.function.name, arguments=tc.function.arguments
                            )
                        )
                usage = completion.usage
                usage_dict2: dict[str, Any] | None = _normalize_usage(usage) if usage else None
                return GenerationResponse(
                    content=content,
                    model=completion.model,
                    finish_reason=choice.finish_reason,
                    tool_calls=tool_calls or None,
                    usage=usage_dict2,
                    reasoning_content=reasoning,
                )
        except Exception as e:
            error_msg = str(e).lower()
            if "rate limit" in error_msg or "too many requests" in error_msg or "429" in error_msg:
                raise RuntimeError(f"Rate limit exceeded: {e!s}") from e
            raise RuntimeError(f"OpenAI tool generation failed: {e!s}") from e

    def get_available_models(self) -> list[str]:
        return ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-4", "gpt-3.5-turbo"]

    def convert_messages_to_dict(self, messages: list[Message]) -> list[dict]:
        result = []
        for msg in messages:
            if msg.image_parts:
                content: list[dict[str, Any]] = [{"type": "text", "text": msg.content}]
                for image_url in msg.image_parts:
                    content.append({"type": "image_url", "image_url": {"url": image_url}})
                result.append({"role": msg.role, "content": content, **(msg.metadata or {})})
            else:
                result.append({"role": msg.role, "content": msg.content, **(msg.metadata or {})})
        return result

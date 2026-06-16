"""OpenAI GPT SDK using the official openai package."""

import asyncio
import logging
import os
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionChunk, ChatCompletionToolParam

from .base import BaseLLMSDK, GenerationConfig, GenerationResponse, Message, ToolCall

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
        ]
    )


async def _retry_on_transient(coro_factory, max_retries: int = _MAX_RETRIES):
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


async def _openai_stream_chunks(
    stream: AsyncIterator[ChatCompletionChunk],
) -> AsyncGenerator[dict[str, Any], None]:
    from ..phase_parser import (
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
                yield {"type": "usage", "usage": chunk.usage.model_dump()}
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            if choice.finish_reason:
                finish_reason = choice.finish_reason
            delta = choice.delta
            reasoning_delta = getattr(delta, "reasoning_content", None) or getattr(
                delta, "reasoning", None
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
    finally:
        if hasattr(stream, "close"):
            try:
                from typing import cast as typing_cast

                await typing_cast(Any, stream).close()
            except Exception:
                pass


class OpenAISDK(BaseLLMSDK):
    # Slugs that natively accept the OpenAI `reasoning_effort` top-level
    # parameter (or its `reasoning: {effort: ...}` sibling on the Responses
    # API). Both work via chat.completions for o-series / gpt-5 family.
    _SLUGS_WITH_REASONING_EFFORT: frozenset[str] = frozenset(
        {"openai", "openai-codex", "openai-responses"}
    )
    # Slugs that speak the OpenRouter `reasoning: {effort: ...}` protocol.
    # Per https://openrouter.ai/docs/api/reference/parameters and the
    # analogous docs from each provider, the nested object form is the
    # canonical one for these gateways. OpenRouter itself rejects requests
    # that include both `reasoning` and `reasoning_effort` (HTTP 400), so
    # we never emit the top-level form for these.
    _SLUGS_WITH_REASONING_OBJECT: frozenset[str] = frozenset(
        {
            "openrouter",
            "kilo",  # documented as OpenRouter-compatible
            "airouter",
            "opencode",
            "ollama",  # accepts both forms; nested is the documented one
            "tokenrouter",  # Responses API style
        }
    )
    # Slugs that need DeepSeek's `extra_body={"thinking": {...}}` toggle.
    _SLUG_DEEPSEEK: str = "deepseek"
    # Slugs that need Zhipu/GLM's `extra_body={"thinking": {...}}` toggle.
    _SLUG_ZHIPU: str = "zhipu"

    def __init__(
        self,
        api_key: str,
        base_url: str | None = None,
        rate_limit_hook=None,
        provider_slug: str | None = None,
    ):
        resolved_url = base_url or "https://api.openai.com/v1"
        if resolved_url.startswith("http://"):
            resolved_url = "https://" + resolved_url[7:]
        super().__init__(api_key, resolved_url)
        self._async_client: AsyncOpenAI | None = None
        self._rate_limit_hook = rate_limit_hook
        self._provider_slug = (provider_slug or "").lower() or None

    @property
    def client(self) -> AsyncOpenAI:
        if self._async_client is None:
            self._async_client = AsyncOpenAI(
                api_key=self.api_key, base_url=self.base_url, timeout=None, max_retries=3
            )
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
            kwargs["max_tokens"] = config.max_tokens
        if config.top_p is not None:
            kwargs["top_p"] = config.top_p
        if config.frequency_penalty is not None and config.frequency_penalty != 0.0:
            kwargs["frequency_penalty"] = config.frequency_penalty
        if config.presence_penalty is not None and config.presence_penalty != 0.0:
            kwargs["presence_penalty"] = config.presence_penalty
        if config.stop_sequences:
            kwargs["stop"] = config.stop_sequences
        if tools:
            kwargs["tools"] = [
                ChatCompletionToolParam(
                    type="function",
                    function={
                        "name": t["function"]["name"],
                        "description": t["function"].get("description", ""),
                        "parameters": t["function"]["parameters"],
                    },
                )
                for t in tools
            ]
            if config.tool_choice is not None:
                kwargs["tool_choice"] = config.tool_choice
        self._apply_thinking_kwargs(kwargs, config)
        return kwargs

    def _apply_thinking_kwargs(self, kwargs: dict[str, Any], config: GenerationConfig) -> None:
        """Translate ``config.thinking_level`` into the provider-specific
        wire parameters documented in the per-provider API references.

        The level ``"none"`` always means "do not request reasoning" and
        is implemented by emitting the provider's specific off-switch
        (omitting params, ``reasoning.exclude``, or ``thinking.type:
        "disabled"``). Models that have no off-switch at all (e.g. some
        on/off-only reasoning models served through a gateway) still
        get a best-effort disable request; if the model cannot honor it
        the API will return an error which surfaces normally to the
        user.
        """
        level = config.thinking_level
        if level is None:
            return
        slug = self._provider_slug

        # --- OpenAI family (native, openai-codex, openai-responses) ---
        if slug in self._SLUGS_WITH_REASONING_EFFORT:
            if level == "none":
                return
            # The OpenAI Python SDK accepts both top-level `reasoning_effort`
            # and the structured `reasoning={effort: ...}` form. The latter
            # is the future-proof form (also covers Responses API).
            if level in ("low", "medium", "high"):
                kwargs["reasoning_effort"] = level
            else:
                # "minimal" / "xhigh" - use the structured form
                kwargs["reasoning"] = {"effort": level}
            return

        # --- OpenRouter-style gateways (openrouter, kilo, airouter,
        # opencode, ollama, tokenrouter) ---
        if slug in self._SLUGS_WITH_REASONING_OBJECT:
            if level == "none":
                kwargs["reasoning"] = {"effort": "none", "exclude": True}
            elif level in ("minimal", "low", "medium", "high", "xhigh"):
                kwargs["reasoning"] = {"effort": level}
            return

        # --- DeepSeek ---
        if slug == self._SLUG_DEEPSEEK:
            if level == "none":
                kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
                return
            # DeepSeek maps low/medium -> high and xhigh -> max.
            effort = "max" if level == "xhigh" else "high"
            kwargs["reasoning_effort"] = effort
            kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
            return

        # --- Zhipu / GLM (on/off only) ---
        if slug == self._SLUG_ZHIPU:
            kwargs["extra_body"] = {
                "thinking": {"type": "disabled" if level == "none" else "enabled"}
            }
            return

        # --- Generic OpenAI-compat gateway fallback ---
        # If a future slug is added that we don't explicitly know about,
        # send the OpenRouter-style nested `reasoning` object. This is
        # the broadest of the documented formats and is the safest
        # default for any OpenAI-compatible gateway.
        if level == "none":
            return
        if level in ("minimal", "low", "medium", "high", "xhigh"):
            kwargs["reasoning"] = {"effort": level}

    async def generate(
        self, messages: list[Message], config: GenerationConfig, stream: bool = False
    ) -> GenerationResponse | AsyncGenerator:
        try:
            kwargs = self._build_kwargs(messages, config)
            kwargs["stream"] = stream
            if stream:
                kwargs["stream_options"] = {"include_usage": True}
                raw_stream = await _retry_on_transient(
                    lambda: self.client.chat.completions.create(**kwargs)
                )
                return _openai_stream_chunks(raw_stream)
            else:

                async def _do_generate():
                    return await self.client.chat.completions.create(**kwargs)

                completion = await _retry_on_transient(_do_generate)
                choice = completion.choices[0]
                msg = choice.message
                content = msg.content or ""
                reasoning = (
                    getattr(msg, "reasoning_content", None) or getattr(msg, "reasoning", "") or ""
                )
                usage = completion.usage
                return GenerationResponse(
                    content=content,
                    model=completion.model,
                    finish_reason=choice.finish_reason,
                    usage=(
                        {
                            "input_tokens": usage.prompt_tokens if usage else 0,
                            "output_tokens": usage.completion_tokens if usage else 0,
                            "total_tokens": usage.total_tokens if usage else 0,
                        }
                        if usage
                        else None
                    ),
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
                raw_stream = await _retry_on_transient(
                    lambda: self.client.chat.completions.create(**kwargs)
                )
                return _openai_stream_chunks(raw_stream)
            else:

                async def _do_generate():
                    return await self.client.chat.completions.create(**kwargs)

                completion = await _retry_on_transient(_do_generate)
                choice = completion.choices[0]
                msg = choice.message
                content = msg.content or ""
                reasoning = (
                    getattr(msg, "reasoning_content", None) or getattr(msg, "reasoning", "") or ""
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
                return GenerationResponse(
                    content=content,
                    model=completion.model,
                    finish_reason=choice.finish_reason,
                    tool_calls=tool_calls or None,
                    usage=(
                        {
                            "input_tokens": usage.prompt_tokens if usage else 0,
                            "output_tokens": usage.completion_tokens if usage else 0,
                            "total_tokens": usage.total_tokens if usage else 0,
                        }
                        if usage
                        else None
                    ),
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

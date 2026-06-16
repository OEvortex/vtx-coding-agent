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


# Slugs that speak the OpenRouter ``reasoning: {effort: ...}`` nested
# protocol (per https://openrouter.ai/docs/api/reference/parameters).
# Every other openai_compat slug uses the bare OpenAI Chat Completions
# wire format: top-level ``reasoning_effort``.
#
# This mirrors opencode's transform.ts: the nested form is reserved
# for the @openrouter/ai-sdk-provider and gateways that document
# themselves as OpenRouter-compatible. Everything else (cerebras,
# togetherai, xai, deepinfra, venice, @ai-sdk/openai-compatible, …)
# uses the bare top-level parameter, because vtx talks to them
# through ``client.chat.completions.create()`` which only accepts
# ``reasoning_effort`` and rejects the structured form with
# ``unexpected keyword argument 'reasoning'``.
#
# Reference:
#   https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/provider/transform.ts
#   https://github.com/openai/openai-python/blob/main/src/openai/resources/chat/completions/completions.py
#   (the SDK signature: ``reasoning_effort: Optional[ReasoningEffort]``,
#   values ``none|minimal|low|medium|high|xhigh``)
_SLUGS_WITH_REASONING_OBJECT: frozenset[str] = frozenset(
    {
        "openrouter",  # @openrouter/ai-sdk-provider
        "kilo",  # documented as OpenRouter-compatible
        "tokenrouter",  # Responses API style (nested object)
    }
)

# Slugs that toggle thinking via ``extra_body={"thinking": {"type": ...}}``
# and don't accept a top-level reasoning_effort. DeepSeek maps
# low/medium -> high and xhigh -> max; Zhipu is on/off only.
_SLUGS_WITH_EXTRA_BODY_THINKING: frozenset[str] = frozenset(
    {
        "deepseek",
        "zhipu",  # GLM
    }
)


def _load_chat_completions_slugs() -> frozenset[str]:
    """Return every openai_compat slug from ``provider.yaml`` that is
    not in :data:`_SLUGS_WITH_REASONING_OBJECT` or
    :data:`_SLUGS_WITH_EXTRA_BODY_THINKING`. These slugs all use the
    OpenAI Chat Completions wire format: bare ``reasoning_effort``
    top-level parameter.

    Loading is lazy and cached so the SDK can be imported without the
    catalog file being available. The set is read from
    ``vtx/llm/provider.yaml`` so a new provider added there
    automatically gets the right treatment without any code change
    here.
    """
    global _chat_completions_cache
    if _chat_completions_cache is not None:
        return _chat_completions_cache

    excluded = _SLUGS_WITH_REASONING_OBJECT | _SLUGS_WITH_EXTRA_BODY_THINKING

    try:
        from ..provider_catalog import list_providers
    except Exception:
        # If the catalog is unimportable (e.g. during a partial
        # install) fall back to the static baseline below.
        _chat_completions_cache = _STATIC_CHAT_COMPLETIONS_SLUGS
        return _chat_completions_cache

    slugs: set[str] = set()
    for p in list_providers():
        if p.family != "openai_compat":
            continue
        if p.slug in excluded:
            continue
        slugs.add(p.slug)
    # Always include the well-known Chat-Completions gateways as a
    # baseline so the behavior is correct even if the catalog is
    # stale or missing.
    slugs.update(_STATIC_CHAT_COMPLETIONS_SLUGS)
    _chat_completions_cache = frozenset(slugs)
    return _chat_completions_cache


# Module-level cache for ``_load_chat_completions_slugs`` so the
# provider catalog is read at most once per process.
_chat_completions_cache: frozenset[str] | None = None


# Static fallback used if the provider catalog cannot be loaded at
# runtime. These are the gateways we explicitly verified to accept the
# bare ``reasoning_effort`` top-level parameter via Chat Completions.
_STATIC_CHAT_COMPLETIONS_SLUGS: frozenset[str] = frozenset(
    {
        # OpenAI native family.
        "openai",
        "openai-codex",
        "openai-responses",
        # Native API providers (OpenAI-compatible endpoints).
        "groq",
        "together",
        "fireworks",
        "mistral",
        "nvidia",
        "deepinfra",
        "huggingface",
        "airouter",  # docs.arouter.com: uses bare reasoning_effort
        "opencode",  # @ai-sdk/openai-compatible (Chat Completions)
        "ollama",  # accepts both; bare reasoning_effort is documented
    }
)


class OpenAISDK(BaseLLMSDK):
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

    def _chat_completions_slugs(self) -> frozenset[str]:
        """Chat-Completions slug set, loaded dynamically from the
        provider catalog (cached at module level). Every openai_compat
        slug in provider.yaml (other than the custom-mapped ones) is
        included automatically.
        """
        return _load_chat_completions_slugs()

    def _apply_thinking_kwargs(self, kwargs: dict[str, Any], config: GenerationConfig) -> None:
        """Translate ``config.thinking_level`` into the wire parameter
        for the current provider slug.

        The dispatch is intentionally tiny:

        - **OpenRouter family** (openrouter / kilo / tokenrouter):
          nested ``reasoning: {effort: ..., exclude: true}`` object.
          Per https://openrouter.ai/docs/api/reference/parameters
          OpenRouter itself rejects requests that include both
          ``reasoning`` and ``reasoning_effort`` with HTTP 400, so we
          never emit the top-level form for these.

        - **DeepSeek / Zhipu (GLM)**: on/off toggle via
          ``extra_body={"thinking": {"type": enabled|disabled}}``.
          DeepSeek additionally accepts ``reasoning_effort`` mapped
          (low/medium -> high, xhigh -> max).

        - **Every other openai_compat slug** (auto-detected from
          provider.yaml, with a static baseline fallback): bare
          top-level ``reasoning_effort`` parameter. This is the
          OpenAI Chat Completions wire format that the openai Python
          SDK accepts. The structured ``reasoning: {effort: ...}``
          form is Responses-API-only and is rejected with
          ``unexpected keyword argument 'reasoning'``.
        """
        level = config.thinking_level
        if level is None:
            return
        slug = self._provider_slug

        # --- OpenRouter family (openrouter, kilo, tokenrouter) ---
        if slug in _SLUGS_WITH_REASONING_OBJECT:
            if level == "none":
                kwargs["reasoning"] = {"effort": "none", "exclude": True}
            elif level in ("minimal", "low", "medium", "high", "xhigh"):
                kwargs["reasoning"] = {"effort": level}
            return

        # --- DeepSeek ---
        if slug == "deepseek":
            if level == "none":
                kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
                return
            effort = "max" if level == "xhigh" else "high"
            kwargs["reasoning_effort"] = effort
            kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
            return

        # --- Zhipu / GLM (on/off only) ---
        if slug == "zhipu":
            kwargs["extra_body"] = {
                "thinking": {"type": "disabled" if level == "none" else "enabled"}
            }
            return

        # --- OpenAI Chat Completions (everything else openai_compat) ---
        if slug in self._chat_completions_slugs():
            if level == "none":
                # Chat Completions has no documented "off" switch for
                # o-series / gpt-5: omitting the parameter lets the
                # model pick its default (medium for older, none for
                # gpt-5.1+). This is the closest we can get to "don't
                # think" via the wire for these families.
                return
            kwargs["reasoning_effort"] = level
            return

        # Unknown slug (not in catalog, not a known family): skip
        # rather than guess. A stale ``last_selected.thinking_level``
        # from a prior model could surface here; dropping silently
        # is safer than emitting a wire param that 400s.

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

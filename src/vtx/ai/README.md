# vtx.ai

The LLM layer, and nothing else. It knows what a provider is, what a model is, what a thinking level means on each wire format, and how to turn one into a stream of `StreamPart`s. The agent harness used to live here too; it is now the sibling package `vtx.agent`, which is what turned this package from a 116-module grab bag into just the provider surface. Consumers are `vtx.agent`, `vtx.coding_agent`, and `vtx.core.config`.

## Usage

Resolving a model and streaming from it. Every step in the chain is a lookup, so the whole path is inspectable before a single token is requested:

```python
import asyncio

from vtx.ai import get_model, get_provider_class, resolve_provider_api_type
from vtx.ai.base import ProviderConfig, resolve_api_key
from vtx.ai.providers.mock import MockProvider
from vtx.protocol.types import UserMessage

# 1. What model is this? The catalog is authoritative and offline.
model = get_model("claude-sonnet-4-5")
print(model.provider, model.id, model.context_window)  # opencode claude-sonnet-4-5 200000

# 2. What wire format does that provider speak, and which class implements it?
api = resolve_provider_api_type(model.provider)          # ApiType('openai-sdk')
provider_cls = get_provider_class(api, model.provider)  # <class '...OpenAISDKProvider'>

# 3. The key. `auto` is the lenient mode: a local base URL needs none, a remote
#    one is optional. Use "required" to make a missing key an error.
key = resolve_api_key(None, base_url=model.base_url, auth_mode="auto")

config = ProviderConfig(
    provider=model.provider,
    model=model.id,
    base_url=model.base_url,
    api_key=key,
    thinking_level="high",
    thinking_level_map=model.thinking_level_map,
)
provider = provider_cls(config)
print(provider.thinking_level, provider.reasoning_style)  # high openai-completions

# To run the streaming half without a key or a network, substitute the bundled
# mock, which takes the same ProviderConfig and yields the same parts.
offline = MockProvider(config)


async def main():
    # `stream` is a coroutine returning an LLMStream, not an async generator:
    # rate-limit retry happens before the stream exists, so you await it first.
    # MockProvider stands in for a live provider here so the example runs offline.
    stream = await offline.stream([UserMessage(content="hello")])
    async for part in stream:
        if part.type == "text":
            print(part.text, end="")
    print()
    print(stream.usage)  # Usage(input_tokens=..., output_tokens=..., ...)
    await stream.aclose()


asyncio.run(main())
```

Swap `offline` for `provider` and the same loop streams from the real API. `BaseProvider.stream` already wraps `rate_limit_manager.retry_stream`, so a 429 with a `Retry-After` header is invisible to the caller; you only see the exception if the retries are exhausted.

## Thinking levels

The interesting part of this package is the reasoning-effort vocabulary, because the same user-facing level ("high") has three different wire spellings and no two providers agree on what the levels are called. `vtx.ai.thinking` is the single source of truth for every picker in the codebase, and it is not re-exported from `vtx.ai` - import it by module.

- `resolve_thinking_levels(*, reasoning, thinking_level_map=None, provider_levels=None, style=None)` is the entry point. It is keyword-only, and it answers "which levels may this picker offer". A non-reasoning model gets `["none"]`; a reasoning model with no catalog map gets `["default"]`; a model whose map is `{"off": None, "minimal": None, "low": "low", "medium": "medium", "high": "high"}` gets `["low", "medium", "high"]` - keys mapped to `None` are catalog-verified as unsupported and are dropped, and `style` filters further to levels that style can actually send.

- `get_supported_thinking_levels(*, reasoning, thinking_level_map=None)` is the same question without the wire-format filter. Use it when you are labelling a control, not building a payload.

- `clamp_thinking_level(level, supported)` is nearest-available fallback for a level restored from a saved session: `clamp_thinking_level("xhigh", ["none", "low", "high"])` is `"high"`. Never let a stale config send a level the model rejects.

- `resolve_reasoning_params(style, level, *, level_map=None, max_tokens=None)` produces the payload. `style` is one of the three module constants `OPENAI_COMPLETIONS` (`"openai-completions"`), `OPENAI_RESPONSES` (`"openai-responses"`), `ANTHROPIC_MESSAGES` (`"anthropic-messages"`), and an unknown value raises `ValueError`. Every `BaseProvider` subclass exposes the right one as `reasoning_style`.

- `parse_models_dev_reasoning_options(options, *, max_tokens=None)` turns a models.dev `reasoning_options` block into a level map. When a model publishes both `effort` and `budget_tokens`, `effort` wins.

```python
from vtx.ai.thinking import resolve_reasoning_params

resolve_reasoning_params("openai-completions", "high")
# {'reasoning_effort': 'high'}

resolve_reasoning_params("openai-responses", "high")
# {'reasoning': {'effort': 'high'}}

# Anthropic has three code paths, chosen by what the catalog verified.
resolve_reasoning_params("anthropic-messages", "high")
# {'thinking': {'type': 'enabled', 'budget_tokens': 8192}}          legacy budget
resolve_reasoning_params("anthropic-messages", "high", level_map={"high": "high"})
# {'thinking': {'type': 'adaptive'}, 'output_config': {'effort': 'high'}}
resolve_reasoning_params("anthropic-messages", "high", level_map={"high": "budget:4096"}, max_tokens=8192)
# {'thinking': {'type': 'enabled', 'budget_tokens': 4096}}

# "default" and an unverifiable "off" both send nothing.
resolve_reasoning_params("openai-completions", "default")   # {}
resolve_reasoning_params("openai-completions", "off")       # {}
resolve_reasoning_params("openai-completions", "off", level_map={"off": "none"})
# {'reasoning_effort': 'none'}
```

That last pair is the subtle one. Omitting the parameter does not stop reasoning - it leaves the model on its own default, which for a reasoning model means thinking. So an "off" level resolves to `{}` only when the catalog verified no literal `none` effort exists to send. `THINKING_LEVELS` and `EXTENDED_THINKING_LEVELS` are the canonical tuples; the difference is that the extended one spells the lowest level `"off"` the way models.dev does, and `DEFAULT_THINKING_LEVELS` in `vtx.ai` is a copy of the first.

## Providers

- `list_providers()` returns the catalog with user overrides from `~/.vtx/providers/*.yaml` merged in, and `get_provider_info(slug)` looks one up, returning `None` for an unknown slug. `ProviderInfo` carries the slug, display name, family, base URL, key env var, known models, capability flags, and the model-parser config for providers that publish `/models`. `provider.yaml` declares 59 providers; the catalog code adds `github-copilot`, `codex`, and `cline` for 62 in `vtx.ai.__all__` terms. `detect_provider_from_env()` infers a provider from whichever API-key env var is set, and is what you want for a `--model`-less launch.

- `resolve_provider_api_type(provider)` maps a slug to an `ApiType` - a validated string wrapper over `openai-sdk`, `openai-responses`, and `anthropic`, raising `ValueError` on anything else. Unknown slugs fall back to `openai-sdk`. `PROVIDER_API_BY_NAME` is the 16-entry table behind it, and it is where the odd cases live: `codex` speaks `openai-responses`, `aerolink` speaks `anthropic`.

- `get_provider_class(api_type, provider_slug="")` returns the concrete `BaseProvider` subclass, with per-slug specialisation. `get_model_class` is not exported; instantiate the class directly.

- `BaseProvider(config)` is the ABC. Concrete subclasses implement `_stream_impl` and `should_retry_for_error`, and inherit `stream`, `chat_with_retry`, `get_default_model`, the `thinking_level` property, `set_thinking_level`, and `cycle_thinking_level`. `is_arrearage_response()` is a classmethod used to detect a provider that reports exhaustion in the body of a 200. The four concrete providers are in `vtx.ai.providers`: `openai_sdk.py` (chat completions), `openai_responses_sdk.py`, `anthropic_sdk.py`, and `mock.py`, plus `sanitize.py` for stray surrogates.

- `ProviderConfig` is what a provider is built from: `api_key`, `base_url`, `model`, `max_tokens`, `temperature`, `thinking_level` (defaults to `"high"`), `provider`, `session_id`, the `openai_compat_auth_mode` / `anthropic_compat_auth_mode` trios (`auto` / `required` / `none`), `default_headers`, and `thinking_level_map`. `GenerationSettings` is the per-run knob bag and is *not* re-exported - import it from `vtx.ai.base`.

- `LLMStream` is the async iterator over `StreamPart` with `.usage`, `.id`, and `.aclose()`. `set_iterator` lets a provider attach an already-running stream. `ApiType` and `Model` are re-exported; `ToolCallRequest` (with `.to_openai_tool_call()`), `LLMResponse`, `resolve_api_key`, `is_local_base_url`, `make_http_client`, and `LOCAL_API_KEY_PLACEHOLDER` are in `vtx.ai.base` only.

- `vtx.ai.sdk` is a second, thinner generation layer (`BaseLLMSDK`, `OpenAISDK`, `AnthropicSDK`) that sits below the providers and also calls `resolve_reasoning_params`.

## Models

- `Model` is the catalog entry: `id`, `provider`, `api`, `base_url`, `max_tokens`, `context_window`, `supports_images`, `supports_thinking`, `supports_tools`, `supports_audio`, `api_model_id`, `is_free`, `thinking_level_map`.

- `get_model(model_id, provider=None)` is the lookup, and its `provider` argument is a *preference*, not a filter: it tries that provider first and then scans all of them. `get_all_models()`, `get_models_by_provider(provider)` and `get_max_tokens(model_id)` round out the static catalog reads, deduped on `(provider, id)`.

- Live discovery is separate. `get_dynamic_models(force_refresh=False)` merges catalog models with `/models` fetches, cached under `~/.vtx/models/` with a 6h TTL; `get_provider_models(provider, *, force_refresh=False)` returns one provider's `DynamicModelEntry` values; `find_dynamic_model(model_id, provider=None)` resolves one. `refresh_provider(provider)` and `refresh_all_providers()` force a fetch and return counts. models.dev lookups are a third source, cached to `models_dev_limits.json` with a 24h TTL, and `context_length.safe_max_output_tokens` keeps a requested output budget inside the model's context window.

- The dynamic provider table is separate from the dynamic *model* cache. `get_dynamic_provider(name)`, `register_dynamic_provider(config)` and `get_dynamic_provider_headers(provider)` operate on `DYNAMIC_PROVIDERS`, a lazy dict of `DynamicProviderConfig` values for providers the user configured at runtime.

- `vtx.ai.model_fetcher` is deprecated in its own docstring: its logic duplicates `dynamic_models`, new code should use `dynamic_models` plus `provider_catalog.get_all_catalog_models`, and it survives only because the catalog still reads its legacy cache format.

## Auth

Four credential families, each with the same shape: a `login` entry point, an `is_*_logged_in` predicate, `get_valid_*` token getters, and `load_*` / `save_*` / `clear_*` for the stored form.

- GitHub Copilot: `login` (aliased `copilot_login`) runs the device flow against github.com or an enterprise domain; `get_copilot_token` is the async getter, `get_valid_copilot_token_sync` the blocking one, `get_valid_copilot_token` resolves a valid token and refreshes it when near expiry.

- OpenAI Codex: `codex_login` reads `~/.codex/auth.json` (path from `get_codex_auth_path`) and `refresh_codex_token` renews it on expiry. `get_valid_codex_credentials` / `_token` / `_token_sync` are the async and sync getters.

- Cline: `cline_login` runs a device flow, `refresh_cline_token` renews, and `get_valid_cline_credentials` / `_token` / `_token_sync` read the result. Cline also ships a list of free model ids.

- Dynamic providers: `load_api_key` / `save_api_key` / `clear_api_key`, `has_api_key`, `get_dynamic_api_key`, and `get_provider_status`, backed by `~/.vtx/dynamic_auth.json`.

Only Copilot has a single async token getter (`get_valid_copilot_token`); Codex and Cline expose both async and `_sync` variants.

## The rest of the surface

- `rate_limit` is transparent backoff. `is_rate_limit_error(exc)` and `parse_retry_after(exc)` classify a failure; `rate_limit_manager` is the shared `RateLimitManager` with `should_retry`, `wait_delay`, `reset`, and `retry_stream`, which is what `BaseProvider.stream` calls around every attempt.

- `tool_parser` recovers tool calls that a gateway inlined into text as `<function=...>` markup: `has_text_tool_calls(content)`, `extract_tool_calls_from_text(content)`, `extract_text_and_tool_calls(content)` (both halves at once), and `normalize_tool_calls(tool_calls)`. Nothing here *executes* a call; this package parses, the harness runs.

- `phase_parser.ThinkingPhaseParser` splits `<think>` blocks out of streaming `delta.content` for gateways that inline their reasoning (DeepSeek R1, Qwen3, GLM), emitting `ThinkStart` / `ThinkDelta` / `ThinkEnd` and `ResponseStart` / `ResponseDelta` / `ResponseEnd` before the TUI's markdown renderer swallows the response. `INLINE_THINK_SIGNATURE` is the delimiter.

- `provider_hooks.prepare_request(*, provider, payload) -> (headers, payload)` is the single interception point every transport calls. Register with `register_headers_listener` / `register_request_listener`, drop with the `unregister_` twins, and reset everything with `reset_listeners`.

## Known defect

`get_valid_token` is listed in `vtx.ai.__all__` but never imported, so `from vtx.ai import *` raises `AttributeError: module 'vtx.ai' has no attribute 'get_valid_token'`. The function does exist as `vtx.ai.oauth.get_valid_token`; the alias that *is* imported is `vtx.ai.get_copilot_token`. Every other name in `__all__` resolves.
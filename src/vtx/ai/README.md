# vtx.ai

The LLM layer: provider catalog, OAuth, streaming SDK adapters, model discovery, rate-limit backoff, and stream parsing.

This package used to hold the agent harness too. The harness is now the sibling package `vtx.agent`; what is left here is everything that talks to a model or knows what a model is.

## Responsibilities

- `provider.yaml` + `provider_catalog.py` - the catalog of LLM providers: slug, base URL, API-key env var, family, capabilities, and the model-parser config for providers that publish `/models`. `provider.yaml` ships **59** providers; `list_providers()` returns more once `~/.vtx/providers/*.yaml` overrides are merged in (63 on this machine - `cline`, `codex`, `github-copilot`, `woino`).
- `oauth/` - credentials for GitHub Copilot (device flow against github.com or an enterprise domain), OpenAI Codex (`~/.codex/auth.json`, refreshed on expiry), Cline (device flow plus a free-model id list), and arbitrary providers (`~/.vtx/dynamic_auth.json`).
- `providers/` - the `BaseProvider` subclasses that actually make requests: `openai_sdk.py` (chat completions), `openai_responses_sdk.py`, `anthropic_sdk.py`, `mock.py`, plus `sanitize.py` for stray surrogates.
- `sdk/` - a second, thinner generation layer (`OpenAISDK`, `AnthropicSDK`, `BaseLLMSDK`) that sits below the providers.
- `models.py` / `dynamic_models.py` / `model_fetcher.py` / `context_length.py` - model discovery: the `Model` dataclass, live `/models` fetching cached under `~/.vtx/models/` (TTL 6h), models.dev lookups cached to `models_dev_limits.json` (TTL 24h), and `safe_max_output_tokens`.
- `thinking.py` - the reasoning-effort vocabulary: which levels a model supports, and how to spell one for each wire format.
- `rate_limit.py` - transparent retry on 429 / `Retry-After` with exponential backoff and jitter.
- `phase_parser.py` - splits `<think>` blocks out of streaming `delta.content` for gateways that inline their reasoning (DeepSeek R1, Qwen3, GLM), before the TUI's markdown renderer swallows the response.
- `tool_parser.py` - recovers tool calls from text-embedded `<function=...>` markup.
- `provider_hooks.py` - `before_provider_headers` / `before_provider_request` interception, shared by every transport.
- `base.py` - the provider-agnostic request/response types and the `BaseProvider` ABC.
- `_httpcore_patch.py` - imported by `base.py` at module import, before any streaming SDK loads, to patch httpcore's `safe_async_iterate`. Wrapped in `try/except`; a failure is silent.

Not responsible for:

- **The agent.** Turns, sessions, tool execution, context assembly - all `vtx.agent`.
- **Session persistence or config.** `vtx.core`.
- **MCP.** `vtx.mcp` talks to servers; this package only models whatever tools the caller hands it.
- **Executing tools.** `vtx.ai.tool_parser` *parses* tool calls out of a stream; nothing here runs them.
- **Tool definitions.** `ToolDefinition`, `Message`, `Usage` and friends come from `vtx.protocol.types`.

## Dependencies

- Imports: standard library, `httpx` (`base.py`, `dynamic_models.py`), `yaml` (`provider_catalog.py`), `aiohttp` (the OAuth modules), and `vtx.protocol.types` (`base.py` imports the wire types: `Message`, `StreamPart`, `TextPart`, `ThinkPart`, `ToolCallDelta`, `ToolCallStart`, `ToolDefinition`, `Usage`, `StreamDone`). So `vtx.ai` sits **above** `vtx.protocol`, not below it.
- Imported by: `vtx.agent` (22 `from vtx.ai import ...` sites plus direct `vtx.ai.base`, `vtx.ai.dynamic_models`, `vtx.ai.provider_catalog`, `vtx.ai.providers`, `vtx.ai.thinking`, `vtx.ai.tool_parser` imports), `vtx.coding_agent`, and one file in `vtx.core` (`vtx.core.config`, for the provider catalog). `vtx.codemode` and `vtx.mcp` do not import it.
- `vtx.ai.base` imports `vtx.ai.thinking`, so importing the base pulls the effort vocabulary in.

Note `vtx.ai.model_fetcher` is deprecated in its own docstring: its logic duplicates `dynamic_models`, new code should use `dynamic_models` plus `provider_catalog.get_all_catalog_models`, and it stays only because the catalog still reads its legacy cache format. `coding_agent` still has 3 import sites against it.

## Public surface

`vtx.ai.__all__` has 62 names, re-exported from the submodules.

### Types and base

| Name | Description |
|------|-------------|
| `BaseProvider(config: ProviderConfig)` | ABC. Concrete: `thinking_level` property + `set_thinking_level` / `cycle_thinking_level`, `stream(...)`, abstract `_stream_impl` and `should_retry_for_error`, `chat_with_retry(...)`, `get_default_model()`, `is_arrearage_response()` (a classmethod). Applies `rate_limit_manager.retry_stream` inside `stream`, so backoff is transparent. |
| `ProviderConfig` | `api_key`, `base_url`, `model`, `max_tokens`, `temperature`, `thinking_level="high"`, `provider`, `session_id`, `openai_compat_auth_mode` / `anthropic_compat_auth_mode` (`auto`\|`required`\|`none`), `default_headers`, `thinking_level_map`. |
| `GenerationSettings` | Per-run knobs: temperature, max_tokens, `reasoning_effort`, `thinking_budget`, stop, penalties, `top_p`, `seed`, `stream`, `response_format`, `json_mode`, `metadata`. Not re-exported from `vtx.ai`; import from `vtx.ai.base`. |
| `LLMStream` | Async iterator over `StreamPart`, with `.usage`, `.id`, `.aclose()`; `set_iterator` lets a provider attach an already-running stream. |
| `Model` | `id`, `provider`, `api`, `base_url`, `max_tokens`, `supports_images`, `supports_thinking`, `context_window`, `supports_tools`, `supports_audio`, `api_model_id`, `is_free`, `thinking_level_map`. |
| `ApiType` | Validated string wrapper over `openai-sdk` / `openai-responses` / `anthropic`; raises `ValueError` otherwise. |
| `DEFAULT_THINKING_LEVELS` | `["none", "minimal", "low", "medium", "high", "xhigh", "max"]`, copied from `thinking.THINKING_LEVELS`. |

Also in `vtx.ai.base`, not re-exported: `ToolCallRequest` (with `.to_openai_tool_call()`), `LLMResponse` (`.should_execute_tools`, `.has_tool_calls`), `resolve_api_key(explicit, *, env_vars=(), base_url=None, auth_mode="required")`, `is_local_base_url(base_url)`, `make_http_client()`, `LOCAL_API_KEY_PLACEHOLDER = "vtx-local"`.

### Provider routing

| Name | Description |
|------|-------------|
| `get_provider_class(api_type, provider_slug="") -> type[BaseProvider]` | Maps an `ApiType` to its class, with per-slug specialisation. |
| `resolve_provider_api_type(provider) -> ApiType` | Unknown slugs fall back to `openai-sdk`. |
| `PROVIDER_API_BY_NAME` | 16 slugs mapped to their wire format (`codex` -> `openai-responses`, `aerolink` -> `anthropic`, everything else `openai-sdk`). |

### Catalog and models

| Name | Description |
|------|-------------|
| `list_providers() -> list[ProviderInfo]` | Catalog entries, user overrides merged. |
| `get_provider_info(slug) -> ProviderInfo \| None` | |
| `detect_provider_from_env() -> ProviderInfo` | Infers the provider from which API-key env var is set. |
| `ProviderInfo` | Slug, display name, description, family, base URL, key env var, known models, capability flags, `fetch_models`, `models_endpoint`, headers, and a `ModelParserConfig`. |
| `get_all_models()` / `get_model(model_id, provider=None)` / `get_models_by_provider(provider)` / `get_max_tokens(model_id)` | Catalog lookups. `get_model` tries `provider_catalog.find_model` first and `dynamic_models.find_dynamic_model` second; a non-`None` `provider` **prefers** that provider but falls back to scanning all of them, so it is a preference, not a filter. `get_all_models` dedupes on `(provider, id)`. |
| `get_dynamic_models(force_refresh=False)` / `get_all_models_with_dynamic(force_refresh=False)` | Catalog models merged with live `/models` fetches. |
| `get_provider_models(provider, *, force_refresh=False) -> list[DynamicModelEntry]` | One provider's fetched models. |
| `find_dynamic_model(model_id, provider=None) -> Model \| None` | |
| `get_dynamic_provider(name)` / `register_dynamic_provider(config)` / `get_dynamic_provider_headers(provider)` | The in-memory dynamic provider table (`DYNAMIC_PROVIDERS`, a lazy dict). |
| `refresh_provider(provider) -> int` / `refresh_all_providers() -> dict[str, int]` | Force a fetch; returns the count per provider. |
| `DynamicModelEntry` | `id`, `name`, `context_window`, `max_tokens`, `supports_images`, `supports_thinking`, `thinking_level_map`, `is_free`, `pricing_known`, `raw`. |
| `DynamicProviderConfig` | `name`, `base_url`, `env_var`, `api`, `headers`, `api_key_optional`, `openmodelendpoint`, `models_endpoint`, `response_format`. |

### OAuth

Copilot: `login` / `copilot_login` (aliased), `get_copilot_token`, `get_valid_copilot_token_sync`, `is_copilot_logged_in`, `load_copilot_credentials`, `clear_copilot_credentials`. Codex: `codex_login`, `get_valid_codex_credentials` / `_token` / `_token_sync`, `refresh_codex_token`, `load_/save_/clear_codex_credentials`, `is_codex_logged_in`, `get_codex_auth_path`. Cline: `cline_login`, `get_valid_cline_credentials` / `_token` / `_token_sync`, `refresh_cline_token`, `load_/clear_cline_credentials`, `is_cline_logged_in`. Dynamic providers: `load_/save_/clear_api_key`, `has_api_key`, `get_dynamic_api_key`, `get_provider_status`.

Only Copilot has an async token getter (`get_valid_copilot_token`); Codex and Cline expose both async and `_sync` variants.

### Thinking levels (`vtx.ai.thinking`, not re-exported)

| Name | Description |
|------|-------------|
| `resolve_thinking_levels(*, reasoning, thinking_level_map=None, provider_levels=None, style=None) -> list[str]` | The single source of truth for every picker. Keyword-only. |
| `get_supported_thinking_levels(*, reasoning, thinking_level_map=None) -> list[str]` | |
| `parse_models_dev_reasoning_options(options, *, max_tokens=None) -> dict[str, str \| None] \| None` | models.dev `reasoning_options` -> level map. `effort` wins over `budget_tokens` when a model publishes both. |
| `clamp_thinking_level(level, supported) -> str` | Nearest-available fallback for a saved level the model cannot do. |
| `resolve_reasoning_params(style, level, *, level_map=None, max_tokens=None) -> dict` | The wire payload for a style (`OPENAI_COMPLETIONS`, `OPENAI_RESPONSES`, `ANTHROPIC_MESSAGES`) and level, including Anthropic's token budgets. |
| `THINKING_LEVELS` / `EXTENDED_THINKING_LEVELS` | `("none", "minimal", ..., "max")` / the same with `"off"` spelled as models.dev does. |

### Rate limits, parsing, hooks

`rate_limit.is_rate_limit_error(exc)`, `parse_retry_after(exc)`, and `rate_limit_manager` (a shared `RateLimitManager` with `should_retry`, `wait_delay`, `reset`, `retry_stream`). `tool_parser.has_text_tool_calls(content)`, `extract_tool_calls_from_text(content)`, `extract_text_and_tool_calls(content)`, `normalize_tool_calls(tool_calls)`. `phase_parser.ThinkingPhaseParser` plus the `ThinkStart`/`ThinkDelta`/`ThinkEnd`/`ResponseStart`/`ResponseDelta`/`ResponseEnd` events and `INLINE_THINK_SIGNATURE`. `provider_hooks.prepare_request(*, provider, payload) -> (headers, payload)`, `register_headers_listener`, `register_request_listener`, their `unregister_` twins, and `reset_listeners`.

## Usage

```python
from vtx.ai import list_providers, get_model, resolve_provider_api_type

# Which providers exist, and what the environment implies.
for p in list_providers():
    if p.supports_tools:
        print(p.slug, p.base_url, p.api_key_env)

print(detect_provider_from_env().slug)          # from whichever API key is set

# Look a model up, then resolve the wire format for the provider you got.
model = get_model("claude-sonnet-4-5")
print(model.provider, resolve_provider_api_type(model.provider))
print(model.context_window, model.thinking_level_map)
```

Streaming is a `BaseProvider` subclass behind `BaseProvider.stream`, which retries rate limits for you:

```python
from vtx.ai.base import BaseProvider, ProviderConfig
from vtx.ai.providers.openai_sdk import OpenAISDKProvider

provider = OpenAISDKProvider(ProviderConfig(provider="openai", model="gpt-4o"))
async for part in provider.stream(messages):
    ...
```

## Known defect

`vtx/ai/__init__.py` lists `get_valid_token` in `__all__` but never imports it, so `from vtx.ai import *` raises `AttributeError: module 'vtx.ai' has no attribute 'get_valid_token'`. The function does exist as `vtx.ai.oauth.get_valid_token`; `vtx.ai.get_copilot_token` is the alias for it that *is* imported. Every other name in `__all__` resolves.
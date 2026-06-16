# Providers and models

Vtx ships with a set of built-in LLM providers and supports adding more through the same OpenAI-compatible surface. This doc covers the full provider list, the auth flows for each, and how to point Vtx at a local model.

## Quick reference

| Provider | `default_provider` | Auth | API | Notes |
| --- | --- | --- | --- | --- |
| OpenAI | `openai` | `OPENAI_API_KEY` | OpenAI Chat Completions | Standard. |
| OpenAI Responses | `openai-responses` | `OPENAI_API_KEY` | OpenAI Responses | Use for `gpt-5.x` Responses API features. |
| OpenAI Codex (OAuth) | `openai-codex` | `/login` (OAuth) | Codex Responses | Uses your ChatGPT Plus/Pro subscription. |
| GitHub Copilot | `github-copilot` | `/login` (OAuth) | GitHub Copilot + Anthropic variant | Mixes Claude (Anthropic transport) and GPT (Responses transport). |
| ZhiPu / ZAI | `zhipu` | `ZAI_API_KEY` or `OPENAI_API_KEY` | OpenAI Chat Completions | Default endpoint `https://api.z.ai/api/coding/paas/v4`. |
| DeepSeek | `deepseek` | `DEEPSEEK_API_KEY` or `OPENAI_API_KEY` | OpenAI Chat Completions | Default endpoint `https://api.deepseek.com`. |
| Azure AI Foundry | `azure-ai-foundry` | `AZURE_AI_FOUNDRY_API_KEY` | Azure-routed Anthropic | Hosts Claude 4.6/4.7. |
| Airouter | `airouter` | `AIROUTER_API_KEY` (env) or `/login` (stored) | OpenAI Chat Completions | Dynamic model catalog. Free tier available. |
| OpenCode | `opencode` | `OPENCODE_API_KEY` (env) or `/login` (stored) | OpenAI Chat Completions | Dynamic model catalog. |
| Kilo | `kilo` | `KILO_API_KEY` (env) or `/login` (stored) | OpenAI Chat Completions | Dynamic model catalog. Free tier available. Auto-injects `X-KILOCODE-EDITORNAME: vtx`. |
| TokenRouter | `tokenrouter` | `TOKENROUTER_API_KEY` (env) or `/login` (stored) | OpenAI Chat Completions | Dynamic model catalog. |

The full provider list is registered in [`src/vtx/llm/providers/__init__.py`](../src/vtx/llm/providers/__init__.py) under `PROVIDER_API_BY_NAME` and [`src/vtx/llm/provider.yaml`](../src/vtx/llm/provider.yaml).

The `provider.yaml` catalog is the single source of truth for static providers. It declares the family (`openai_compat` or `anthropic`), the canonical `base_url`, the env var holding the API key, and an optional `fetch_models: true` flag so the model list is auto-refreshed from `<base_url><models_endpoint>` (default `/v1/models`) and cached to `~/.vtx/models/<slug>.json`. When a static provider also has `fetch_models: true`, the dynamic catalog takes precedence and `known_models` is used only as a fallback when the network is unavailable.

## Built-in providers

### OpenAI (`openai`)

- **Endpoint:** `https://api.openai.com/v1`
- **Auth:** `OPENAI_API_KEY`
- **API:** Chat Completions
- **Use it for:** any OpenAI Chat Completions model. Compatible with any third-party service that exposes an OpenAI-compatible `/v1` endpoint — set `llm.default_base_url` (or `--base-url`) to point at it.

### OpenAI Responses (`openai-responses`)

- **Endpoint:** `https://api.openai.com/v1`
- **Auth:** `OPENAI_API_KEY`
- **API:** OpenAI Responses
- **Use it for:** models that should use the Responses API (e.g. when you want built-in tools/state that the Responses API exposes).

### OpenAI Codex (`openai-codex`)

- **Endpoint:** `https://chatgpt.com/backend-api` (resolved from OAuth)
- **Auth:** OAuth via `/login`
- **API:** Codex Responses (websocket with SSE fallback)
- **Use it for:** using a ChatGPT Plus/Pro subscription through Vtx without a separate API key. The OAuth flow opens your browser to `auth.openai.com`, captures the redirect on `http://localhost:1455/auth/callback`, and stores the resulting credentials in `~/.vtx/openai_auth.json` (mode `0600`).

The OAuth flow refreshes access tokens automatically when they get within 60 seconds of expiry.

### GitHub Copilot (`github-copilot`)

- **Endpoint:** `https://api.individual.githubcopilot.com` (resolved from token at runtime)
- **Auth:** OAuth via `/login` (GitHub device flow)
- **API:** Mixed — Claude models use an Anthropic Messages transport (`ANTHROPIC_COPILOT`), GPT models use a GitHub-Copilot-flavored OpenAI Responses transport (`GITHUB_COPILOT_RESPONSES`). The static model table in [`src/vtx/llm/models.py`](../src/vtx/llm/models.py) maps each model to the correct transport.
- **Use it for:** models made available through a GitHub Copilot subscription. The auth flow stores the token in `~/.vtx/copilot_auth.json` (mode `0600`).

The login flow reuses the official `gh` CLI's authentication if `gh auth login` has already been run, so you can skip the device flow entirely by being logged in to `gh` first.

### ZhiPu / ZAI (`zhipu`)

- **Endpoint:** `https://api.z.ai/api/coding/paas/v4`
- **Auth:** `ZAI_API_KEY` (preferred) or `OPENAI_API_KEY`
- **API:** OpenAI Chat Completions
- **Models:** `glm-5.1`, `glm-5.2`. The ZAI provider is what Vtx uses for Zhipu GLM models.

### DeepSeek (`deepseek`)

- **Endpoint:** `https://api.deepseek.com`
- **Auth:** `DEEPSEEK_API_KEY` (preferred) or `OPENAI_API_KEY`
- **API:** OpenAI Chat Completions
- **Models:** `deepseek-v4-flash`, `deepseek-v4-pro`.

### Azure AI Foundry (`azure-ai-foundry`)

- **Endpoint:** `AZURE_AI_FOUNDRY_BASE_URL` (required)
- **Auth:** `AZURE_AI_FOUNDRY_API_KEY`
- **API:** Azure-routed Anthropic
- **Models:** `claude-sonnet-4.6-azure`, `claude-opus-4.6-azure`, `claude-opus-4.7-azure`. Useful for organizations on Azure that need Claude through a managed endpoint.

### Aerolink (`aerolink`)

- **Endpoint:** `https://capi.aerolink.lat`
- **Auth:** `ANTHROPIC_API_KEY` env var or `--api-key` flag (the Anthropic SDK transport only looks at `ANTHROPIC_API_KEY`, so the same key works whether you point at Anthropic directly or at the Aerolink gateway).
- **API:** Anthropic Messages (`/v1/messages`)
- **Models:** auto-fetched from `https://capi.aerolink.lat/v1/models`, cached for 60 minutes at `~/.vtx/models/aerolink.json`. Vision and thinking support are enabled by default; the gateway itself decides which Claude variants it exposes.
- **Use it for:** routing Anthropic traffic through the Aerolink gateway (e.g. regional access, metered billing, or a custom proxy).

To enable:

```bash
export ANTHROPIC_API_KEY="sk-aerolink-..."
vtx --provider aerolink --model <model from /model picker>
```

## Dynamic catalog providers

Vtx's four most recent providers (`airouter`, `opencode`, `kilo`, `tokenrouter`) don't hard-code a model list. They fetch it from the gateway's `GET /v1/models` endpoint, parse it, and cache it locally. The list is then merged into the static model table and shown in the `/model` picker.

### Why dynamic?

Modern LLM gateways rotate their model catalogs frequently — sometimes weekly. Hard-coding a list in `models.py` would go stale the day it ships. The dynamic providers keep a fresh catalog and degrade gracefully when the network is slow or the gateway is down.

### How it works

1. On first lookup, Vtx calls `<base_url>/models` with a 10-second timeout.
2. The response is parsed and normalized into `DynamicModelEntry` records with: `id`, `name`, `context_window`, `max_tokens`, `supports_images`, `supports_thinking`, `is_free`.
3. The result is cached to `~/.vtx/models/<provider>.json` with a 6-hour TTL.
4. Subsequent lookups return the cache (stale-while-revalidate).
5. On network or auth errors, Vtx falls back to whatever is cached locally. If nothing is cached, the provider is unavailable until the next refresh.

You can force a refresh at any time with `/model refresh [provider]`. Refreshing with no `provider` arg refreshes all four.

### Provider details

| Provider | Base URL | Free tier | Special headers |
| --- | --- | --- | --- |
| `airouter` | `https://api.airouter.in/v1` | yes | none |
| `opencode` | `https://opencode.ai/zen/v1` | no | none |
| `kilo` | `https://api.kilo.ai/api/gateway` | yes | `X-KILOCODE-EDITORNAME: vtx`, `User-Agent: vtx` |
| `tokenrouter` | `https://api.tokenrouter.com/v1` | no | none |

### Auth priority

For dynamic providers, Vtx checks the key in this order:

1. `<NAME>_API_KEY` env var (e.g. `KILO_API_KEY`).
2. Stored key at `~/.vtx/dynamic_auth.json` (written by `/login`).
3. For free-tier providers (`airouter`, `kilo`): a placeholder key, so the gateway still returns a response.

The stored file is JSON or YAML (whichever exists first), written with mode `0600`. You can manage entries with `/login <provider>` and `/logout <provider>`.

### Free-model detection

Vtx uses a dual heuristic to mark a model as "free" in the picker:

- If the gateway exposes pricing, a model is free iff `prompt == 0` and `completion == 0` (or its name contains `"free"`).
- If the gateway doesn't expose pricing, a model is free iff its name contains `"free"` (case-insensitive).

This mirrors the convention used by the pi free-models extension, so anyone used to that workflow gets the same behavior.

## Selecting a provider and model

Three ways, in priority order:

1. **CLI flag** (per-run override):

   ```bash
   vtx --provider openai-codex --model gpt-5.5
   vtx --provider deepseek --model deepseek-v4-flash
   vtx --provider kilo --model <id from /model picker>
   ```

2. **Config** (persistent default):

   ```yaml
   llm:
     default_provider: "deepseek"
     default_model: "deepseek-v4-flash"
   ```

3. **In-app picker** (`/model`): shows the merged static + dynamic catalog. Arrow keys to navigate, Enter to select. Hidden models (via `ui.hidden_models`) don't appear. To scope the picker to a single provider, use `/provider` or set `ui.model_provider_filter` directly.

## OAuth flows in detail

Both built-in OAuth providers store credentials under `~/.vtx/`:

| Provider | File | Refresh |
| --- | --- | --- |
| OpenAI Codex | `openai_auth.json` | Automatic (refresh token) |
| GitHub Copilot | `copilot_auth.json` | Automatic (via `gh` if available, otherwise Copilot device flow) |

### OpenAI Codex login

```text
1. Run /login
2. Pick "OpenAI" from the picker
3. Browser opens https://auth.openai.com/oauth/authorize...
4. Sign in + approve
5. Browser redirects to http://localhost:1455/auth/callback
6. Vtx captures the code, exchanges it for tokens, saves credentials
7. /login shows "logged in as <account-id>"
```

If port `1455` is busy (or you're SSHed in with no browser access), the flow falls back to a "paste the redirect URL" prompt. Either way, the resulting credentials are written to `~/.vtx/openai_auth.json`.

### GitHub Copilot login

```text
1. Run /login
2. Pick "GitHub Copilot" from the picker
3. If `gh` is logged in, Vtx reuses that token
4. Otherwise: device flow — display a code, ask you to visit https://github.com/login/device
5. Save the token to ~/.vtx/copilot_auth.json
```

The Copilot token gets re-resolved against the GitHub API on each new session to pick the right base URL (`api.individual.githubcopilot.com` vs. `api.business.githubcopilot.com`).

## Auth modes for OpenAI/Anthropic-compatible endpoints

When Vtx talks to a non-OpenAI / non-Anthropic-native provider, it has to decide whether to demand an API key. The decision is controlled by `llm.auth.openai_compat` and `llm.auth.anthropic_compat`:

| Mode | Behavior |
| --- | --- |
| `auto` (default) | Inject a placeholder key when the base URL is local (`localhost` / `127.0.0.1` / non-routable). Demand a real key for public hosts. |
| `none` | Always send a placeholder. Use this for local servers that ignore the key. |
| `required` | Always demand a real key, even on localhost. |

CLI equivalents: `--openai-compat-auth` and `--anthropic-compat-auth` (one run only).

## Local models

See [local-models.md](local-models.md) for the full guide. Short version:

```bash
# 1. Start llama-server (or any OpenAI-compatible local server)
llama-server --model /path/to/model.gguf --port 5000 --ctx-size 32768 --gpu-layers all

# 2. Point Vtx at it
vtx --provider openai --base-url http://localhost:5000/v1 \
    --model unsloth/Qwen3.5-27B-GGUF \
    --openai-compat-auth none
```

For local models, you typically also want to lower `compaction.threshold_percent` so compaction fires well below the model's real context window. See [local-models.md](local-models.md) for a worked example.

## Adding a provider (programmatic)

Vtx isn't a closed system. To add a new OpenAI-compatible provider at runtime, use the public function:

```python
from vtx.llm import register_dynamic_provider
from vtx.llm.dynamic_models import DynamicProviderConfig
from vtx.llm.models import ApiType

register_dynamic_provider(
    DynamicProviderConfig(
        name="my-gateway",
        base_url="https://my-gateway.example/v1",
        env_var="MY_GATEWAY_API_KEY",
        api=ApiType.OPENAI_COMPLETIONS,
    )
)
```

After that, the gateway is queryable through `--provider my-gateway` and via `/model`. The catalog will be fetched and cached the same way as the four built-in dynamic providers.

For deeper changes (a non-OpenAI API, OAuth, etc.), see [architecture.md](architecture.md) and the existing provider implementations in [`src/vtx/llm/providers/`](../src/vtx/llm/providers/).

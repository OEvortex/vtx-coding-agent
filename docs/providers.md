# Providers & models

## Built-in catalog

`src/vtx/ai/provider.yaml` defines **59 providers**. Each entry carries a slug, display name, base URL, API-key env var, known models, capability flags (tools/vision/thinking), and an optional dynamic model-catalog endpoint.

Highlights:

- **First-party**: `openai`, `anthropic`, `deepseek`, `zhipu` (Z.ai), `mistral`, `moonshot`, `minimax`, `nvidia`
- **Aggregators**: `openrouter`, `together`, `fireworks`, `groq`, `deepinfra`, `huggingface`, `modelscope`, `baseten`, `friendli`, `clarifai`, `vercelai`
- **Free/community**: `pollinations`, `chutes`, `nanogpt`, `freemodel`, `blackbox`
- **Local**: `ollama` (see [local-models.md](local-models.md))
- **Special**: `opencode`, `kilo`, plus several community gateways

Run `vtx` and use `/provider` then `/model` to browse; `/model` auto-fetches each provider's live catalog when available.

## API keys

Keys resolve in this order: config/CLI → provider env var → OAuth (if the provider supports it) → local-endpoint bypass. Logged-in credentials are cached as JSON/YAML files under `~/.vtx` (e.g. `copilot_auth.json`).

Env vars recognized out of the box (`src/vtx/ai/base.py`):

| Provider | Env var |
| --- | --- |
| openai | `OPENAI_API_KEY` |
| anthropic | `ANTHROPIC_API_KEY` |
| deepseek | `DEEPSEEK_API_KEY` |
| zhipu | `ZAI_API_KEY` |
| openrouter | `OPENROUTER_API_KEY` |
| airouter | `AIROUTER_API_KEY` |
| opencode | `OPENCODE_API_KEY` |
| kilo | `KILO_API_KEY` |
| tokenrouter | `TOKENROUTER_API_KEY` |
| zyloo | `ZYLOO_API_KEY` |
| opengateway | `OPENGATEWAY_API_KEY` |

Other providers prompt for their key in the TUI (`/login`) or accept it via `--api-key/-k`. Keys from OAuth and interactive logins are stored under `~/.vtx/auth/`.

Base URLs on localhost / loopback are treated as local: no API key is required (a `vtx-local` placeholder is sent instead).

## OAuth logins

Built-in login flows (`src/vtx/ai/oauth/`):

- **GitHub Copilot** — `vtx` → `/login` → copilot; device flow, token refresh handled automatically.
- **OpenAI (Codex)** — ChatGPT-style OAuth used by the default `openai-codex` provider.
- **Cline (WorkOS)** — OAuth login for the Cline provider with token refresh and free-model detection.
- **Dynamic providers** — any catalog provider flagged for OAuth gets a generated login via `/login`.

`/logout <provider>` clears stored credentials.

## Custom providers

Drop a YAML file into `~/.vtx/providers/*.yaml` (user-wide) or `.vtx/providers/*.yaml` (project-local, wins on collision). Same schema as one catalog entry:

```yaml
slug: my-gateway
display_name: My Gateway
description: OpenAI-compatible internal gateway.
family: openai_compat          # or anthropic_compat
base_url: https://llm.internal.example.com/v1
api_key_env: MY_GATEWAY_API_KEY
known_models: [internal-mini, internal-max]
supports_tools: true
supports_vision: false
api_key_optional: false
fetch_models: true             # GET {base_url}/models to build the catalog
```

Custom providers appear in `/model` immediately — no code changes.

## Any OpenAI/Anthropic-compatible endpoint

Point any built-in family at your endpoint:

```bash
vtx --provider openai --base-url http://localhost:8080/v1 \
    --openai-compat-auth none -m my-model
```

`--anthropic-compat-auth` behaves the same for Anthropic-style endpoints. Auth modes: `auto` (key if present), `required` (fail without key), `none`.

## Dynamic catalogs & limits

- Model lists are fetched live from provider endpoints or [models.dev](https://models.dev) and cached ~6 h in `~/.vtx/models/`.
- Context-window/output limits come from models.dev cached 24 h (`models_dev_limits.json`); unknown models fall back to `agent.default_context_window`.

## Thinking levels

Levels cycle with `ctrl+t`: `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max` — but vtx only ever *offers* the ones the selected model advertises, detected per-model from the [models.dev](https://models.dev) reasoning options and intersected with what the transport can express:

- models that publish named efforts (OpenAI, Claude 4.6+) get exactly those tiers — `gpt-5-pro` offers only `high`, `gpt-5.6` offers up to `max`;
- models that publish a thinking *token budget* instead of efforts (Claude Haiku/Sonnet 4.5) get a budget ladder, which the Anthropic API sends as `thinking.budget_tokens`;
- models the catalog describes only as reasoning-capable, or not at all, offer `default` — vtx sends no reasoning parameter and lets the model decide, instead of guessing an effort that might be rejected.

A level the current model doesn't support is never sent: it is clamped to the nearest supported one, so switching models or restoring a session can't fail a request.

### Model ids are matched exactly

`get_model()` looks a model id up **verbatim**. It does not fuzzy-match, strip a
namespace, or try suffixes. Because 3,735 of the 6,361 catalog ids are
namespace-prefixed (`stealth/space-bunny-alpha`) and only 2,626 are bare
(`space-bunny-alpha`), an id that misses the catalog silently falls back to the
provider's raw effort enum instead of the catalog's per-model reasoning options —
so the offered levels are whatever that provider accepts, not what the model's
catalog entry says it supports.

`/thinking` surfaces this rather than failing quietly: when the catalog entry is
missing it reports `catalog entry not found, check the exact model id`. Copy the
id from the `/model` picker instead of typing it from memory.

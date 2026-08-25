# Providers & models

## Built-in catalog

`src/ai/provider.yaml` defines **57 providers**. Each entry carries a slug, display name, base URL, API-key env var, known models, capability flags (tools/vision/thinking), and an optional dynamic model-catalog endpoint.

Highlights:

- **First-party**: `openai`, `anthropic`, `deepseek`, `zhipu` (Z.ai), `mistral`, `moonshot`, `minimax`, `nvidia`
- **Aggregators**: `openrouter`, `together`, `fireworks`, `groq`, `deepinfra`, `huggingface`, `modelscope`, `baseten`, `friendli`, `clarifai`, `vercelai`
- **Free/community**: `pollinations`, `chutes`, `nanogpt`, `freemodel`, `blackbox`
- **Local**: `ollama` (see [local-models.md](local-models.md))
- **Special**: `opencode`, `kilo`, plus several community gateways

Run `vtx` and use `/provider` then `/model` to browse; `/model` auto-fetches each provider's live catalog when available.

## API keys

Keys resolve in this order: config/CLI → provider env var → OAuth (if the provider supports it) → local-endpoint bypass. Logged-in credentials are cached as JSON/YAML files under `~/.vtx` (e.g. `copilot_auth.json`).

Env vars recognized out of the box (`src/ai/base.py`):

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

Built-in login flows (`src/ai/oauth/`):

- **GitHub Copilot** — `vtx` → `/login` → copilot; device flow, token refresh handled automatically.
- **OpenAI (Codex)** — ChatGPT-style OAuth used by the default `openai-codex` provider.
- **Supercode** — hosted gateway with its own token flow.
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

Levels cycle with `ctrl+t`: `none`, `minimal`, `low`, `medium`, `high`, `xhigh` — the provider advertises which subset it supports; unsupported requests fall back to its default.

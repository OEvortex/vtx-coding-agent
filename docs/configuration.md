# Configuration

Vtx's configuration lives in a single YAML file at:

```text
~/.vtx/config.yml
```

The file is created automatically on first run from the defaults in [`src/vtx/defaults/config.yml`](../src/vtx/defaults/config.yml). Old schemas are migrated forward automatically with a timestamped backup. The full migration history is at the bottom of this doc.

All keys are optional — anything you omit is filled in from the shipped defaults. This doc lists every key, its default, its validation rule, and the CLI flag (if any) that overrides it.

## Top-level shape

```yaml
meta:
  config_version: 6

llm:
  default_provider: "openai-codex"
  default_model: "gpt-5.5"
  default_base_url: ""
  default_thinking_level: "low"
  tool_call_idle_timeout_seconds: 180
  request_timeout_seconds: 600
  auth:
    openai_compat: "auto"
    anthropic_compat: "auto"
  tls:
    insecure_skip_verify: false
  system_prompt:
    git_context: true
    content: ""

compaction:
  on_overflow: "continue"
  buffer_tokens: 20000

agent:
  max_turns: 500
  default_context_window: 200000

ui:
  theme: "gruvbox-dark"
  collapse_thinking: true
  thinking_lines: "1"
  colored_tool_badge: true
  show_welcome_shortcuts: true
  hidden_models: []

permissions:
  mode: "prompt"

notifications:
  enabled: false
  volume: 0.5
```

## `meta`

| Key | Type | Default | Notes |
| --- | --- | --- | --- |
| `config_version` | int | `6` | The schema version Vtx was built against. Auto-bumped on migration. Do not edit. |

## `llm`

### `llm.default_provider`

| | |
| --- | --- |
| Default | `"openai-codex"` |
| CLI | `--provider` |
| Allowed values | `openai`, `openai-responses`, `openai-codex`, `github-copilot`, `zhipu`, `deepseek`, `azure-ai-foundry`, `airouter`, `opencode`, `kilo`, `tokenrouter` |

See [providers.md](providers.md) for what each one does.

### `llm.default_model`

| | |
| --- | --- |
| Default | `"gpt-5.5"` |
| CLI | `--model` / `-m` |

Any model id from the active provider. Dynamic providers (`airouter`, `opencode`, `kilo`, `tokenrouter`) accept any model id from the gateway's `/v1/models` endpoint.

### `llm.default_base_url`

| | |
| --- | --- |
| Default | `""` (use the provider's built-in endpoint) |
| CLI | `--base-url` / `-u` |

Override the provider endpoint. The most common use is pointing a local OpenAI-compatible server (llama-server, vLLM, LM Studio, etc.) at one of the standard providers — see [local-models.md](local-models.md).

### `llm.default_thinking_level`

| | |
| --- | --- |
| Default | `"low"` |
| Slash | `/thinking` |
| Allowed values | `"none"`, `"minimal"`, `"low"`, `"medium"`, `"high"`, `"xhigh"` |

The standardized thinking effort sent to providers that support it. Models that don't support thinking ignore this value. See the README's "Standardized thinking levels" note and [providers.md](providers.md) for which models support which levels.

### `llm.tool_call_idle_timeout_seconds`

| | |
| --- | --- |
| Default | `180` |
| Type | float (seconds) |

How long Vtx waits for a single tool call to produce output before declaring it stalled. Increase this for long-running shell commands that legitimately go quiet for minutes.

### `llm.request_timeout_seconds`

| | |
| --- | --- |
| Default | `600` (10 minutes) |
| Type | float (seconds) |

Hard timeout on the entire LLM request, including all tool calls. Set higher for very long agent runs that hit a max-turns boundary.

### `llm.auth.openai_compat` and `llm.auth.anthropic_compat`

| | |
| --- | --- |
| Default | `"auto"` |
| CLI | `--openai-compat-auth`, `--anthropic-compat-auth` |
| Allowed values | `"auto"`, `"required"`, `"none"` |

Policy for how strictly the local OpenAI/Anthropic-compatible transport enforces an API key.

| Mode | Behavior |
| --- | --- |
| `"auto"` | Inject a placeholder key when the endpoint looks local (`localhost` / `127.0.0.1` / non-routable address). Demand a real key for any public host. |
| `"none"` | Always send a placeholder. Use this for local servers that ignore the key. |
| `"required"` | Always demand a real key, even on localhost. |

### `llm.tls.insecure_skip_verify`

| | |
| --- | --- |
| Default | `false` |
| CLI | `--insecure-skip-verify` (boolean flag) |

Skip TLS certificate verification. Use for self-signed local providers (e.g. a local llama-server behind a reverse proxy).

### `llm.system_prompt.git_context`

| | |
| --- | --- |
| Default | `true` |

When true, a snapshot of `git status` is appended to the system prompt at session start. It does not update during the conversation; ask the model to re-run `git status` for current state.

### `llm.system_prompt.content`

| | |
| --- | --- |
| Default | `""` (use the Python default from `vtx.prompts.identity`) |

The base identity + general rules for the agent. Leave empty to use the built-in default. If you set this, you are overriding Vtx's prompt entirely; the `AGENTS.md` / skills / git / env sections are still appended automatically.

See [`src/vtx/prompts/identity.py`](../src/vtx/prompts/identity.py) for the shipped default and how it composes from named sections.

## `compaction`

### `compaction.on_overflow`

| | |
| --- | --- |
| Default | `"continue"` |
| Allowed values | `"continue"`, `"pause"` |

What Vtx does when the conversation overflows the model's context window. `"continue"` compacts and keeps going; `"pause"` compacts and stops the agent loop. You can also trigger compaction manually with `/compact` regardless of this setting.

### `compaction.buffer_tokens`

| | |
| --- | --- |
| Default | `20000` |

Tokens of headroom kept free near the context-window limit. Compaction triggers when the running total is within `buffer_tokens` of the model's `context_window`. For local models with smaller windows, see [local-models.md](local-models.md) for a worked example.

## `agent`

### `agent.max_turns`

| | |
| --- | --- |
| Default | `500` |

Hard cap on the number of agentic turns (assistant message + tool calls + tool results) per session. Reaching the cap terminates the loop. The non-interactive `-p` mode returns exit code `3` in that case.

### `agent.default_context_window`

| | |
| --- | --- |
| Default | `200000` |

Fallback context window for models that don't declare one. If a model in [providers.md](providers.md) specifies its own `context_window`, that wins.

## `ui`

### `ui.theme`

| | |
| --- | --- |
| Default | `"gruvbox-dark"` |
| Slash | `/themes` |

One of the built-in themes listed in [theming.md](theming.md). Unknown values are rejected at config-load time.

### `ui.collapse_thinking`

| | |
| --- | --- |
| Default | `true` |

When true, finalized thinking blocks are collapsed to a one-line summary (or `thinking_lines` lines). When false, the full thinking content is shown inline.

### `ui.thinking_lines`

| | |
| --- | --- |
| Default | `"1"` |
| Allowed values | `"1"`, `"2"`, `"3"`, `"4"`, `"5"`, `"none"` |

How many lines to show when collapsed. `"none"` shows the full thinking content (equivalent to `collapse_thinking: false` for display purposes, but still cached).

### `ui.colored_tool_badge`

| | |
| --- | --- |
| Default | `true` |

When true, the tool name and icon in the chat header use the theme's `badge.label` color. When false, the badge uses a dimmer neutral color.

### `ui.show_welcome_shortcuts`

| | |
| --- | --- |
| Default | `true` |

When true, the welcome panel on launch lists keyboard shortcuts. Set to `false` to hide the panel.

### `ui.hidden_models`

| | |
| --- | --- |
| Default | `[]` |

List of models hidden from the `/model` picker. Use a provider name to hide all its models (`"github-copilot"`) or `"provider:model"` to hide a single model (`"github-copilot:gpt-5.5-copilot"`). Hidden models stay usable via config defaults or `--model` / session resume — they just don't show up in the picker.

## `extensions`

### `extensions` (list of paths)

Paths to Python extension files or package directories. Each entry is loaded at startup in addition to the auto-discovered paths in `<cwd>/.vtx/extensions/` and `~/.vtx/agent/extensions/`.

```yaml
extensions:
  - ~/.vtx/extensions/permission_gate.py
  - ./tools/vtx-extensions/audit-logger/
```

Pass `--no-extensions` on the CLI to skip auto-discovery (the explicit list still loads). See [extensions.md](extensions.md) for the full extension API.

## `permissions`

### `permissions.mode`

| | |
| --- | --- |
| Default | `"prompt"` |
| Slash | `/permissions` |
| Allowed values | `"prompt"`, `"auto"` |

- `"prompt"` — ask before any mutating tool call (subject to the safe-command allowlist in [permissions.md](permissions.md)).
- `"auto"` — skip approval prompts entirely. Read-only tools are still read-only.

## `notifications`

### `notifications.enabled`

| | |
| --- | --- |
| Default | `false` |
| Slash | `/notifications` |

When true, plays an audio chime when a task finishes, errors out, or needs approval. The shipped notification is a short WAV in `vtx/notify.py`.

### `notifications.volume`

| | |
| --- | --- |
| Default | `0.5` |
| Range | `0.0`–`1.0` |

Master volume for the chime. Set `0.0` to keep the on/off semantics without audio (equivalent to disabled for sound purposes; you can still re-enable with `/notifications`).

On Windows, the volume control is not honored (only the on/off toggle is) — see the platform notes in the README.

## `last_selected`

Persisted by Vtx so that `/model` and `/thinking` pickers start at the last value you chose. You don't usually edit this by hand.

| Key | Type | Notes |
| --- | --- | --- |
| `model_id` | str \| null | Last selected model id |
| `provider` | str \| null | Last selected provider |
| `thinking_level` | str \| null | Last selected thinking level |

## Environment variables

Vtx reads a few env vars that mirror config keys, plus provider-specific credentials.

| Env var | Mirrors | Notes |
| --- | --- | --- |
| `XDG_CONFIG_HOME` | `~/.config` base | When set, Vtx stores config under `~/.vtx/` instead of `~/.vtx/`. |
| `VTX_MODELS_CACHE_DIR` | `~/.vtx/models` | Override the dynamic provider model cache location. |
| `OPENAI_API_KEY` | (credential) | OpenAI / OpenAI-compatible API key fallback. |
| `DEEPSEEK_API_KEY` | (credential) | Preferred over `OPENAI_API_KEY` for the `deepseek` provider. |
| `ZAI_API_KEY` | (credential) | Preferred over `OPENAI_API_KEY` for the `zhipu` provider. |
| `AZURE_AI_FOUNDRY_API_KEY` | (credential) | Required for the `azure-ai-foundry` provider. |
| `AZURE_AI_FOUNDRY_BASE_URL` | `llm.default_base_url` (Azure only) | The Azure AI Foundry endpoint. |
| `AIROUTER_API_KEY` | (credential) | For the `airouter` dynamic provider. Optional — provider supports a free tier. |
| `OPENCODE_API_KEY` | (credential) | For the `opencode` dynamic provider. |
| `KILO_API_KEY` | (credential) | For the `kilo` dynamic provider. Optional — provider supports a free tier. |
| `TOKENROUTER_API_KEY` | (credential) | For the `tokenrouter` dynamic provider. |

Env-var credentials beat the stored-on-disk credentials for the dynamic providers; for the OAuth providers (`github-copilot`, `openai-codex`) the OAuth flow is the only path.

## Migration history

Vtx auto-migrates your config when you upgrade. The current `config_version` is `6`. Each migration:

1. Reads the existing YAML.
2. Applies the version-bump in memory.
3. Backs up the old file as `config.yml.bak.<timestamp>`.
4. Writes the migrated file atomically.

| From → To | What changed |
| --- | --- |
| v0 → v1 | Initial schema. |
| v1 → v2 | (No data shape changes — version pin.) |
| v2 → v3 | Removed inline `ui.colors`, defaulted `ui.theme` to `gruvbox-dark`. |
| v3 → v4 | Added `llm.auth.openai_compat` and `llm.auth.anthropic_compat` (defaulted to `"auto"`). |
| v4 → v5 | Added `notifications.volume` (defaulted to `0.5`). |
| v5 → v6 | Promoted `llm.system_prompt` to a dict with `git_context` and `content`. The default `content` is now sourced from `vtx.prompts.identity` so users can override it via config. |
| v6 → v7 | Added top-level `extensions:` (list of paths). Default is `[]`. Auto-discovered paths in `.vtx/extensions/` and `~/.vtx/agent/extensions/` still load unless `--no-extensions` is passed. |

The earlier Vtx releases stored the config under `~/.vtx/`. v0.3.11 added a migration that moves `config.yml`, `sessions/`, `auth/` files, and `models/` into `~/.vtx/`. The old path is no longer read. See [`src/vtx/config.py`](../src/vtx/config.py) for the migration code.

## Validation

Vtx validates the file with Pydantic at load time. Bad values produce a launch warning on stderr and fall back to the built-in defaults. Common mistakes:

| Symptom | Cause |
| --- | --- |
| `Invalid theme: <name>` | `ui.theme` is not a built-in theme id. See [theming.md](theming.md). |
| `Invalid config values at <path>` | A field failed Pydantic validation (wrong type, out-of-range enum, etc.). Read the message; it points at the bad key. |
| `Migrated config at <path> from vX to vY` | Auto-migration succeeded; a backup was written. |
| `Failed to persist migrated config at <path>` | Migration ran in memory but the disk write failed. Your next run will migrate again. |

## Programmatic access

The runtime config is exposed via `vtx.get_config()` (cached in a `ContextVar` for the running process). Tests and embedders can swap it with `vtx.set_config(cfg)` and `vtx.reset_config()`. See [architecture.md](architecture.md) for the full module map.

# Configuration

Vtx stores config in `~/.vtx/config.yml` (created with defaults on first run). Every field below is verified against `src/coding_agent/defaults/config.yml` (schema version 12).

## `llm`

| Field | Default | Notes |
| --- | --- | --- |
| `default_provider` | `"openai-codex"` | Any provider slug from [providers.md](providers.md) or a custom provider |
| `default_model` | `"gpt-5.5"` | Model ID passed to the provider |
| `default_base_url` | `""` | Override the provider's endpoint (local models etc.) |
| `default_thinking_level` | `"low"` | One of the provider's supported thinking levels (`none`, `minimal`, `low`, `medium`, `high`, `xhigh`) |
| `tool_call_idle_timeout_seconds` | `180` | Abort a stalled tool-call stream after this idle time |
| `request_timeout_seconds` | `600` | HTTP request timeout |
| `auth.openai_compat` | `"auto"` | `auto` / `required` / `none` — whether OpenAI-compatible endpoints need an API key |
| `auth.anthropic_compat` | `"auto"` | Same for Anthropic-compatible endpoints |
| `tls.insecure_skip_verify` | `false` | Skip TLS verification (self-signed certs on local providers). CLI: `--insecure-skip-verify` |
| `system_prompt.content` | `""` | Custom base prompt; empty uses the built-in identity. Extra sections are still appended |
| `system_prompt.git_context` | `true` | Attach a git status/diff snapshot to the system prompt |

## `compaction`

| Field | Default | Notes |
| --- | --- | --- |
| `on_overflow` | `"continue"` | `continue` auto-compacts; `pause` stops and asks |
| `threshold_percent` | `80` | Compact when context usage crosses this % of the window |

The window is the active model's real context window from the catalog (e.g. 1M-class models compact at ~800k, not at the fallback). `agent.default_context_window` applies only when the model has no known window.

## `agent`

| Field | Default | Notes |
| --- | --- | --- |
| `max_turns` | `500` | Hard turn budget per run |
| `default_context_window` | `200000` | Fallback when the model's window is unknown |

## `ui`

| Field | Default | Notes |
| --- | --- | --- |
| `theme` | `"gruvbox-dark"` | See [theming.md](theming.md); `/settings` → themes |
| `collapse_thinking` | `true` | Collapse reasoning blocks when done |
| `thinking_lines` | `"1"` | Visible reasoning lines: `"1"`–`"5"` or `"none"` |
| `colored_tool_badge` | `true` | Tint tool badges with theme colors |
| `show_welcome_shortcuts` | `true` | Shortcut hints on the welcome panel |
| `hidden_models` | `[]` | Model IDs hidden from `/model` |
| `model_provider_filter` | `""` | Preselect a provider filter in `/model` |

## `permissions`

| Field | Default | Notes |
| --- | --- | --- |
| `mode` | `"prompt"` | `prompt` (approve mutations) or `auto`. Toggle live with `alt+ctrl+p`; see [permissions.md](permissions.md) |

## `notifications`

| Field | Default | Notes |
| --- | --- | --- |
| `enabled` | `false` | Sound on completion/permission/error events |
| `volume` | `0.5` | 0.0–1.0 |

## `recap`

Automatic session recap. After an agent run finishes and you haven't typed for a while (or when you resume a session), vtx drafts a 1–3 sentence "where you left off" summary using the current model and renders it in the chat log. Cleared on the next prompt; also available any time via `/recap`.

| Field | Default | Notes |
| --- | --- | --- |
| `enabled` | `true` | Set to `false` to disable automatic recaps (`/recap` still works) |
| `idle_seconds` | `30` | Idle time after a run before a recap is drafted (minimum 5) |

## `extensions`

List of extra extension paths (a `.py` file or package dir), additive to auto-discovery:

```yaml
extensions:
  - ~/.vtx/extensions/audit-logger.py
```

Auto-discovered paths (`<cwd>/.vtx/extensions/`, `~/.vtx/agent/extensions/`) always load unless `--no-extensions` is passed. See [extensions.md](extensions.md).

## `agents`

```yaml
agents:
  default: ""        # agent activated at start when --agent / VTX_AGENT unset
  switch_mode: lock  # lock | hot
  files: []          # extra agent files, like extensions:
```

- `lock` (default): switching starts a new session JSONL preserving lineage.
- `hot`: switching re-renders system prompt + tools in place next turn.

See [agents.md](agents.md).

## `task`

Built-in sub-agent presets for the `task` tool. Each preset accepts: `description`, `instructions`, `instructions_mode` (`append`/`replace`), `tools_allow`/`tools_deny`, `model`, `thinking_level`, `max_turns`. Defaults define `general-purpose`, `Explore` and `Plan` — see [tools.md](tools.md#task).

## Internal state

`last_selected` (model/provider/thinking level/agent) and `recent_models` are written by the TUI; edit manually at your own risk.

## Loading & migration

Config is deep-merged over defaults, then migrated through versioned migrations (`meta.config_version`, currently 12). Migrations back up the old file before writing. Invalid YAML falls back to defaults with a warning shown at launch.

## CLI overrides

Most session-level fields have flags: `--model/-m`, `--provider`, `--api-key/-k`, `--base-url/-u`, `--openai-compat-auth`, `--anthropic-compat-auth`, `--insecure-skip-verify`, `--agent/-a`. See `vtx --help`.

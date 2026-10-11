# Configuration

Vtx keeps one user-level configuration file, `~/.vtx/config.yml`, written as YAML. This page covers where that file lives, every field in the schema with its real default, how CLI flags and environment variables override it, and how the file is migrated when the schema changes.

## Where the config lives

The config directory is resolved by `get_config_dir()` in `src/vtx/core/paths.py:7`. The directory name is the constant `CONFIG_DIR_NAME = "vtx"` (`src/vtx/core/config.py:40`).

| Order | Source | Resulting config file |
|---|---|---|
| 1 | `XDG_CONFIG_HOME` is set and absolute | `$XDG_CONFIG_HOME/vtx/config.yml` (`src/vtx/core/paths.py:13`) |
| 2 | `HOME` is set and is not `/` | `$HOME/.vtx/config.yml` (`src/vtx/core/paths.py:20`) |
| 3 | `Path.home()` resolves to a non-`/` path | `~/.vtx/config.yml` (`src/vtx/core/paths.py:27`) |
| 4 | The pwd database entry for the current uid | `~/.vtx/config.yml` (`src/vtx/core/paths.py:34`) |
| 5 | Fallback | `<cwd>/.vtx/config.yml` (`src/vtx/core/paths.py:44`) |

Vtx creates the file the first time the config is loaded. `_ensure_config_file()` writes the contents of `src/vtx/core/defaults/config.yml` to `~/.vtx/config.yml` if the file does not exist (`src/vtx/core/config.py:389`). Nothing else writes this file except the setters described below, so you can edit it by hand.

There is no project-level config file. Project-scoped settings live in other files: `.vtx/mcp.json`, `.vtx/hooks.yml`, `.vtx/extensions/`, `.vtx/agent/`, and `.vtx/skills/` (`src/vtx/tui/app.py:384`, `src/vtx/core/config.py:250`).

Other paths under the config directory that the code reads and writes:

| Path | Responsibility |
|---|---|
| `~/.vtx/providers/*.yaml` | Custom provider definitions (`src/vtx/ai/provider_catalog.py:436`) |
| `~/.dynamic_auth` (see below) | API keys pasted through `/login` |
| `~/.vtx/models/<slug>.json` | Cached provider model catalogs (`src/vtx/ai/model_fetcher.py:56`) |
| `~/.vtx/models_dev_limits.json` | Cached models.dev context/output limits (`src/vtx/ai/context_length.py:52`) |
| `~/.vtx/codex_auth.json`, `~/.vtx/openai_auth.json` | OpenAI Codex OAuth credentials (`src/vtx/ai/oauth/codex.py:67`) |
| `~/.vtx/copilot_auth.json` | GitHub Copilot OAuth credentials (`src/vtx/ai/oauth/copilot.py:48`) |
| `~/.vtx/cline_auth.json` | Cline OAuth credentials (`src/vtx/ai/oauth/cline.py:68`) |
| `~/.vtx/config.yml.bak.<YYYYMMDDHHMMSS>` | Backup written before an in-place migration (`src/vtx/core/config.py:837`) |

Keys pasted through `/login` for a keyed provider are stored in the dynamic auth file. `get_dynamic_auth_path()` prefers `~/vtx/dynamic_auth.yml`, then `$XDG_CONFIG_HOME/vtx/dynamic_auth.json`, then `~/.vtx/dynamic_auth.json` (`src/vtx/ai/oauth/dynamic.py:52`).

## Config version and migration

Every config file carries `meta.config_version`. The current version is read out of the shipped defaults file at import time: `CURRENT_CONFIG_VERSION = int(_DEFAULT_CONFIG_DATA.get("meta", {}).get("config_version", 13))` (`src/vtx/core/config.py:65`). `src/vtx/core/defaults/config.yml` sets it to `16`.

On load, `_migrate_config_data()` walks the file forward one step at a time until it reaches the current version (`src/vtx/core/config.py:739`). If anything changed and the file had content, Vtx copies the original to `config.yml.bak.<timestamp>` and rewrites the file atomically, then records a warning you can read with `consume_config_warnings()` and see in the TUI launch warnings (`src/vtx/core/config.py:881`).

The migration chain and what each step does:

| From | To | Change |
|---|---|---|
| 0 | 1 | Stamp `meta.config_version` (`src/vtx/core/config.py:436`) |
| 1 | 2 | Stamp `meta.config_version` (`src/vtx/core/config.py:446`) |
| 2 | 3 | Add `ui.theme = "gruvbox-dark"`, drop a legacy `ui.colors` block (`src/vtx/core/config.py:456`) |
| 3 | 4 | Add `llm.auth.openai_compat` and `llm.auth.anthropic_compat`, both `"auto"` (`src/vtx/core/config.py:474`) |
| 4 | 5 | Add `notifications.volume`, default `0.5` (`src/vtx/core/config.py:497`) |
| 5 | 6 | Drop `system_prompt.content`, add `system_prompt.git_context` (`src/vtx/core/config.py:514`) |
| 6 | 7 | Add the top-level `extensions:` list, coercing the older `{paths: [...]}` dict shape (`src/vtx/core/config.py:537`) |
| 7 | 8 | Add `ui.model_provider_filter`, default `""` (`src/vtx/core/config.py:563`) |
| 8 | 9 | Add the `agents:` block and `last_selected.agent` (`src/vtx/core/config.py:582`) |
| 9 | 10 | No-op. Sub-agent presets removed (`src/vtx/core/config.py:615`) |
| 10 | 11 | No-op. Goal config removed (`src/vtx/core/config.py:628`) |
| 11 | 12 | Add the `recent_models` block (`src/vtx/core/config.py:640`) |
| 12 | 13 | Add a top-level `mode` key (`src/vtx/core/config.py:659`) |
| 13 | 14 | Drop the top-level `mode` key. The RLM runtime mode is gone (`src/vtx/core/config.py:675`) |
| 14 | 15 | Drop `task.subagent_presets`, add `task.max_concurrent: 4` (`src/vtx/core/config.py:695`) |
| 15 | 16 | Drop the `refine` block (`src/vtx/core/config.py:720`) |

A migration never silently rewrites a value you should have to agree to: `mode` and `refine` were deleted rather than normalised so a stale key cannot sit in the file suggesting a choice that no longer exists (`src/vtx/core/config.py:684`).

If the file is unreadable YAML, or a value fails pydantic validation, Vtx records a warning and falls back to the built-in defaults rather than refusing to start (`src/vtx/core/config.py:862`, `src/vtx/core/config.py:892`).

## Field reference

All sections of `ConfigSchema` (`src/vtx/core/config.py:239`) are listed below. Defaults are the values in the pydantic classes, which match `src/vtx/core/defaults/config.yml`. The schema carries almost no `Field(description=...)` text; the descriptions below are taken from the code comments in `src/vtx/core/config.py` and `src/vtx/core/defaults/config.yml`.

### `meta`

| Field | Type | Default | Description |
|---|---|---|---|
| `config_version` | int | `16` | Schema version, used by the migration chain (`src/vtx/core/config.py:75`) |

### `llm`

| Field | Type | Default | Description |
|---|---|---|---|
| `default_provider` | str | `"openai-codex"` | Provider slug used at session start when nothing else selects one (`src/vtx/core/config.py:131`, `src/vtx/core/defaults/config.yml:5`) |
| `default_model` | str | `"gpt-5.5"` | Model id used at session start (`src/vtx/core/config.py:132`, `src/vtx/core/defaults/config.yml:6`) |
| `default_base_url` | str | `""` | Base URL override. Empty means "use the provider's own" (`src/vtx/core/config.py:133`) |
| `default_thinking_level` | str | `"low"` | Starting reasoning effort (`src/vtx/core/config.py:134`) |
| `tool_call_idle_timeout_seconds` | float | `180` | Idle timeout for a tool call (`src/vtx/core/config.py:136`) |
| `request_timeout_seconds` | float | `600` | Request timeout, also used for the HTTP client when TLS verification is off (`src/vtx/core/config.py:137`, `src/vtx/ai/base.py:131`) |
| `auth` | `AuthConfig` | `AuthConfig()` | Compatibility-endpoint auth modes (`src/vtx/core/config.py:138`) |
| `tls` | `TLSConfig` | `TLSConfig()` | TLS settings (`src/vtx/core/config.py:139`) |
| `system_prompt` | `SystemPromptConfig` | required | System-prompt toggles (`src/vtx/core/config.py:135`) |

### `llm.system_prompt`

| Field | Type | Default | Description |
|---|---|---|---|
| `git_context` | bool | `true` | Include repository git context in the system prompt (`src/vtx/core/config.py:117`) |
| `ponytail` | bool | `false` | Enable ponytail mode (`src/vtx/core/config.py:118`) |

The legacy key `llm.system_prompt_git_context` is folded into `system_prompt.git_context` on load (`src/vtx/core/config.py:312`).

### `llm.auth`

| Field | Type | Default | Description |
|---|---|---|---|
| `openai_compat` | `"auto"` \| `"required"` \| `"none"` | `"auto"` | Auth mode for OpenAI-compatible endpoints (`src/vtx/core/config.py:122`) |
| `anthropic_compat` | `"auto"` \| `"required"` \| `"none"` | `"auto"` | Auth mode for Anthropic-compatible endpoints (`src/vtx/core/config.py:123`) |

See [providers.md](providers.md#auth-modes-for-compatible-endpoints) for what each mode does.

### `llm.tls`

| Field | Type | Default | Description |
|---|---|---|---|
| `insecure_skip_verify` | bool | `false` | Skip TLS certificate verification (`src/vtx/core/config.py:127`) |

When true, `make_http_client()` returns an `httpx.AsyncClient` with `verify=False` instead of returning `None` (which would let each SDK build its own verifying client) (`src/vtx/ai/base.py:125`).

### `ui`

| Field | Type | Default | Description |
|---|---|---|---|
| `theme` | str | `"gruvbox-dark"` | Theme id. Validated against the shipped theme list; an unknown name fails validation (`src/vtx/core/config.py:83`, `src/vtx/core/config.py:103`) |
| `collapse_thinking` | bool | `true` | Collapse finalized thinking blocks to a one-line summary (`src/vtx/core/config.py:86`) |
| `thinking_lines` | `"1"` \| `"2"` \| `"3"` \| `"4"` \| `"5"` \| `"none"` | `"1"` | Lines shown when thinking is collapsed. `"none"` means no truncation (`src/vtx/core/config.py:88`) |
| `colored_tool_badge` | bool | `true` | Tool icon and name use the badge label color on success (`src/vtx/core/config.py:90`) |
| `show_welcome_shortcuts` | bool | `true` | Show the keyboard-shortcuts panel in the welcome section (`src/vtx/core/config.py:93`) |
| `hidden_models` | list[str] | `[]` | Models hidden from `/model`. A bare provider slug hides all of its models; `"provider:model"` hides one (`src/vtx/core/config.py:97`) |
| `model_provider_filter` | str | `""` | Restrict `/model` to a single provider slug. Empty shows every provider (`src/vtx/core/config.py:101`) |

### `compaction`

| Field | Type | Default | Description |
|---|---|---|---|
| `on_overflow` | `"continue"` \| `"pause"` | `"continue"` | What happens when the context window overflows (`src/vtx/core/config.py:143`) |
| `threshold_percent` | float | `80.0` | Percentage of the context window that triggers overflow compaction (`src/vtx/core/config.py:144`) |

Overflow is computed as `total_tokens >= (threshold_percent / 100) * context_window` (`src/vtx/core/compaction.py:180`).

### `agent`

| Field | Type | Default | Description |
|---|---|---|---|
| `max_turns` | int | `500` | Maximum agent turns (`src/vtx/core/config.py:148`) |
| `default_context_window` | int | `200000` | Context window used when the catalog has no entry for the active model (`src/vtx/core/config.py:149`, `src/vtx/agent/loop.py:556`) |

### `permissions`

| Field | Type | Default | Description |
|---|---|---|---|
| `mode` | `"prompt"` \| `"auto"` | `"prompt"` | `"prompt"` asks before mutating tool calls, `"auto"` allows them (`src/vtx/core/config.py:153`, `src/vtx/core/permissions.py:186`) |

See [permissions.md](permissions.md) for the approval flow.

### `notifications`

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `false` | Play notification sounds (`src/vtx/core/config.py:157`) |
| `volume` | float | `0.5` | Volume, constrained `ge=0.0, le=1.0` (`src/vtx/core/config.py:158`) |

### `recap`

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `true` | Draft an idle "where you left off" summary using the current model (`src/vtx/core/config.py:164`) |
| `idle_seconds` | float | `60` | Idle time before the recap is drafted, constrained `ge=5` (`src/vtx/core/config.py:165`) |

### `last_selected`

| Field | Type | Default | Description |
|---|---|---|---|
| `model_id` | str \| null | `null` | Last selected model (`src/vtx/core/config.py:169`) |
| `provider` | str \| null | `null` | Last selected provider (`src/vtx/core/config.py:170`) |
| `thinking_level` | str \| null | `null` | Last selected thinking level (`src/vtx/core/config.py:171`) |
| `agent` | str \| null | `null` | Last selected handoff agent (`src/vtx/core/config.py:172`) |

This block is written by vtx, not by you. `set_last_selected()` records it on every model, thinking-level, or agent change (`src/vtx/core/config.py:1128`).

### `recent_models`

| Field | Type | Default | Description |
|---|---|---|---|
| `entries` | list of `{provider, model_id}` | `[]` | Recently selected models, most recent first, deduped, capped at 10 (`src/vtx/core/config.py:181`, `src/vtx/core/config.py:1195`) |

Entries with a falsy `model_id` are dropped on load instead of failing the config (`src/vtx/core/config.py:183`).

### `extensions`

| Field | Type | Default | Description |
|---|---|---|---|
| `extensions` | list[str] | `[]` | Extra `.py` files or package directories to load as extensions. Directories `.vtx/extensions/` and `~/.vtx/agent/extensions/` are always loaded too, unless `--no-extensions` is passed (`src/vtx/core/config.py:254`) |

See [extensions.md](extensions.md) and `/plugin` profiles in [plugins.md](plugins.md).

### `agents`

| Field | Type | Default | Description |
|---|---|---|---|
| `default` | str | `""` | Agent activated at session start when `--agent` and `VTX_AGENT` are both unset. Empty means no agent (`src/vtx/core/config.py:214`) |
| `switch_mode` | `"lock"` \| `"hot"` | `"lock"` | How Shift+Tab and `/agent <name>` switch. `"lock"` starts a new session JSONL; `"hot"` re-renders the system prompt and tools in place (`src/vtx/core/config.py:215`) |
| `files` | list[str] | `[]` | Extra agent-file paths. `.vtx/agent/` and `~/.vtx/agent/` are always discovered (`src/vtx/core/config.py:219`) |

### `task`

| Field | Type | Default | Description |
|---|---|---|---|
| `max_concurrent` | int | `4` | Cap on concurrent `Task` sub-agents. Constrained `ge=0`; `0` disables the cap and everything runs at once (`src/vtx/core/config.py:236`) |

Sub-agent profiles are not configurable here. A `subagent_type` is resolved against the agents in `.vtx/agent/<name>.py`; an unknown name falls back to a bare sub-agent with the parent's tool surface (`src/vtx/core/config.py:222`).

## Example config

This file is valid YAML, parses against `ConfigSchema`, and only changes fields from their shipped defaults.

```yaml
meta:
  config_version: 16

llm:
  default_provider: "anthropic"
  default_model: "claude-sonnet-4-20250514"
  default_base_url: ""
  default_thinking_level: "medium"
  tool_call_idle_timeout_seconds: 180
  request_timeout_seconds: 600

  auth:
    openai_compat: "auto"
    anthropic_compat: "auto"

  tls:
    insecure_skip_verify: false

  system_prompt:
    git_context: true
    ponytail: false

compaction:
  on_overflow: "continue"
  threshold_percent: 80

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
  model_provider_filter: ""

permissions:
  mode: "prompt"

notifications:
  enabled: false
  volume: 0.5

recap:
  enabled: true
  idle_seconds: 60

extensions: []

agents:
  default: ""
  switch_mode: "lock"
  files: []

task:
  max_concurrent: 4

last_selected:
  model_id: null
  provider: null
  thinking_level: null
  agent: null

recent_models:
  entries: []
```

To point at a local Ollama server instead, change `default_provider` to `ollama`, `default_model` to a model that server serves, and set the auth mode so no key is required:

```yaml
llm:
  default_provider: "ollama"
  default_model: "qwen3-coder"
  default_base_url: "http://localhost:11434/v1"
  auth:
    openai_compat: "none"
```

`"none"` short-circuits key resolution to a placeholder, so the request goes out with a dummy `Authorization` header (`src/vtx/ai/base.py:151`). `"auto"` reaches the same result because `is_local_base_url()` treats `localhost`, `127.0.0.1`, `::1`, `0.0.0.0`, `*.local`, and RFC1918/link-local addresses as local (`src/vtx/ai/base.py:102`).

## Precedence

There is no layered config: one YAML file, plus CLI flags that win over it, plus a small number of environment variables. Where two sources both apply, the CLI flag wins.

### Model, provider, thinking level

| Setting | Order of resolution |
|---|---|
| model | `--model` > `last_selected.model_id` > `llm.default_model` (`src/vtx/tui/app.py:145`, `src/vtx/coding_agent/headless.py:161`) |
| provider | `--provider` > `last_selected.provider` > `llm.default_provider` (only when `--model` was not passed) (`src/vtx/tui/app.py:146`) |
| thinking level | `last_selected.thinking_level` > `llm.default_thinking_level`. There is no CLI flag for this (`src/vtx/tui/app.py:159`) |
| base URL | `--base-url` > `llm.default_base_url` > the provider's own `base_url` from the catalog (`src/vtx/tui/app.py:156`, `src/vtx/agent/runtime.py:939`) |
| API key | `--api-key` only. There is no config-file fallback and no `last_selected` fallback; after that the provider's env var, then a key stored by `/login` (`src/vtx/tui/app.py:155`, `src/vtx/ai/providers/openai_sdk.py:49`) |
| OpenAI-compat auth mode | `--openai-compat-auth` > `llm.auth.openai_compat` (`src/vtx/tui/app.py:162`) |
| Anthropic-compat auth mode | `--anthropic-compat-auth` > `llm.auth.anthropic_compat` (`src/vtx/tui/app.py:165`) |
| agent | `--agent` > `VTX_AGENT` > `last_selected.agent` > `agents.default` (`src/vtx/tui/app.py:261`) |

Resumption overrides the model side of this: a resumed session records its own provider, model, and base URL, and those win unless you pass an explicit override (`src/vtx/agent/runtime.py:1024`).

Headless mode (`-p/--prompt`) forces `permissions.mode = "auto"` for the run and writes that back to the config file, restoring the previous value afterwards. Approval prompts cannot be shown without a TUI (`src/vtx/coding_agent/headless.py:150`).

### Environment variables

| Variable | Effect |
|---|---|
| `VTX_PROVIDER` | Selects the provider before any API key is inspected by `detect_provider_from_env()` (`src/vtx/ai/provider_catalog.py:266`) |
| `VTX_MODEL` | Fallback model id inside the OpenAI SDK layers (`src/vtx/ai/sdk/openai.py:298`) |
| `VTX_BASE_URL` | Overrides the default OpenAI SDK base URL (`src/vtx/agent/runtime.py:61`) |
| `VTX_AGENT` | Selects the active handoff agent (`src/vtx/tui/app.py:260`) |
| `VTX_PROFILE` | Plugin profile to boot (`src/vtx/plugins/bootstrap.py:38`) |
| `VTX_MODELS_CACHE_DIR` | Directory for cached provider catalogs (`src/vtx/ai/model_fetcher.py:57`) |
| `XDG_CONFIG_HOME` | Relocates the config directory (`src/vtx/core/paths.py:13`) |

Provider API keys are environment variables too, one per provider; the full list is in the provider table in [providers.md](providers.md#the-catalog).

## Changing settings from inside the TUI

`/settings` opens a picker over nine rows: `colored-tool-badge`, `git-context`, `notifications`, `ponytail`, `show-shortcuts`, `permissions`, `themes`, `thinking`, and `thinking-lines` (`src/vtx/tui/commands/settings.py:234`). Each row writes through a dedicated setter in `src/vtx/core/config.py` that reads the file, edits one key, stamps the config version, and rewrites the file atomically before reloading.

| Setting | Setter | Slash command |
|---|---|---|
| `ui.theme` | `set_theme()` (`src/vtx/core/config.py:954`) | `/themes [id]` (`src/vtx/tui/commands/settings.py:51`) |
| `ui.thinking_lines` | `set_thinking_lines()` (`src/vtx/core/config.py:1005`) | `/settings` > `thinking-lines` (`src/vtx/tui/commands/settings.py:187`) |
| `ui.show_welcome_shortcuts` | `set_show_welcome_shortcuts()` (`src/vtx/core/config.py:973`) | `/settings` > `show-shortcuts` |
| `ui.colored_tool_badge` | `set_colored_tool_badge()` (`src/vtx/core/config.py:1063`) | `/settings` > `colored-tool-badge` |
| `ui.model_provider_filter` | `set_model_provider_filter()` (`src/vtx/core/config.py:1095`) | `/provider` (`src/vtx/tui/commands/providers.py:15`) |
| `permissions.mode` | `set_permissions_mode()` (`src/vtx/core/config.py:989`) | `/permissions [prompt\|auto]` (`src/vtx/tui/commands/settings.py:76`) |
| `notifications.enabled` | `set_notifications_enabled()` (`src/vtx/core/config.py:1079`) | `/notifications [on\|off]` (`src/vtx/tui/commands/settings.py:209`) |
| `llm.system_prompt.git_context` | `set_git_context()` (`src/vtx/core/config.py:1021`) | `/settings` > `git-context` |
| `llm.system_prompt.ponytail` | `set_ponytail()` (`src/vtx/core/config.py:1042`) | `/ponytail [on\|off]` (`src/vtx/tui/commands/settings.py:279`) |

`set_model_provider_filter()` drops an unknown slug instead of persisting it (`src/vtx/core/config.py:1104`). After editing the file by hand, run `/reload`, which re-reads the config, extensions, agents, tools, and skills, and re-applies a theme change to the live CSS (`src/vtx/tui/commands/reload.py:243`).

## `vtx --dump-config`

`--dump-config` does not print the config file. It prints the composed plugin row list for the selected profile, one row per line as `<id>\t[enabled|disabled]\t<name>` (`src/vtx/coding_agent/cli.py:149`).

```bash
vtx --dump-config
vtx --dump-config --profile headless
```

```text
tools	[enabled]	vtx.plugins.builtins.services.tools:PLUGIN
llm	[enabled]	vtx.plugins.builtins.services.llm:PLUGIN
permissions	[enabled]	vtx.plugins.builtins.services.permissions:PLUGIN
tool-read	[enabled]	vtx.plugins.builtins.tools.read:PLUGIN
```

The listing includes extension rows discovered at boot, which are not stored in the profile (`src/vtx/coding_agent/cli.py:227`). Every row printed here is replaceable through a plugin patch; see [plugins.md](plugins.md).

To read the actual configuration, print the file:

```bash
cat ~/.vtx/config.yml
```

Use `XDG_CONFIG_HOME` if you relocated the config directory:

```bash
cat "${XDG_CONFIG_HOME:-$HOME/.config}/vtx/config.yml"
```

## Known gaps

- There is no `vtx --dump-config` (or equivalent flag) that prints the merged, validated config. To see the resolved values you have to read `~/.vtx/config.yml` yourself, and a value that failed validation silently falls back to the built-in default (`src/vtx/core/config.py:892`).
- `ConfigSchema` carries no `Field(description=...)` text, so nothing generates a schema reference. The defaults in `src/vtx/core/defaults/config.yml` are the only machine-readable summary and they omit several fields that have defaults in code, such as `ui.collapse_thinking`, `ui.thinking_lines`, `ui.colored_tool_badge`, and the nullable `last_selected` keys (`src/vtx/core/defaults/config.yml:31`).
- The unknown-key story is inconsistent: unknown keys survive the deep merge into `ConfigSchema.model_validate` and are ignored, while unknown *values* for a known key (an unknown theme name, a bad float) fail validation for the whole file and drop you back to defaults (`src/vtx/core/config.py:323`).
- `llm.default_thinking_level` is a free-form string with no validation, so a typo is only caught later, when the provider clamps it (`src/vtx/ai/base.py:273`).
- `--api-key` has no config-file counterpart. A key can only come from the flag, the environment, or `/login` (`src/vtx/tui/app.py:155`).
- Migration warnings are collected in a module-level list and consumed by the first caller. Anything that loads the config before the TUI starts can swallow them (`src/vtx/core/config.py:69`).
- There is no project-level config file, so per-repository model or permission defaults require an agent file in `.vtx/agent/` or a plugin patch rather than a config value.

Related pages: [providers.md](providers.md), [theming.md](theming.md), [cli.md](cli.md), [permissions.md](permissions.md), [skills.md](skills.md), [plugins.md](plugins.md).

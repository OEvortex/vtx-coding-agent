# Configuration

agenite-claw's configuration lives in a single JSON file at:

```text
~/.vtx/claw/config.json
```

The file is created automatically by `agenite-claw onboard`. All keys are optional — anything you omit uses the shipped defaults.

## Environment Variable Interpolation

Any string field supports `${VAR_NAME}` syntax. The variable is resolved at load time:

```json
{
  "channels": {
    "telegram": {
      "token": "${TELEGRAM_BOT_TOKEN}"
    }
  }
}
```

If the variable is not set, loading fails with a clear error.

## Top-Level Shape

```json
{
  "agents": { ... },
  "channels": { ... },
  "providers": { ... },
  "tools": { ... },
  "gateway": { ... },
  "api": { ... },
  "transcription": { ... },
  "model_presets": { ... }
}
```

## `agents`

Agent-level configuration.

### `agents.defaults`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `workspace` | string | `"~/.vtx/claw/workspace"` | Workspace directory for sessions, memory, and files |
| `model` | string | `"anthropic/claude-sonnet-4-20250514"` | Default LLM model |
| `provider` | string | `"auto"` | Provider name or `"auto"` for auto-detection |
| `max_tokens` | int | `8192` | Maximum output tokens per response |
| `context_window_tokens` | int | `200000` | Context window size in tokens |
| `context_block_limit` | int \| null | `null` | Max tokens for context blocks |
| `temperature` | float | `0.1` | Sampling temperature |
| `fallback_models` | list | `[]` | Fallback models when primary fails |
| `max_tool_iterations` | int | `200` | Max tool call iterations per turn |
| `max_concurrent_subagents` | int | `1` | Max concurrent subagent spawns |
| `fail_on_tool_error` | bool | `true` | Stop on tool execution error |
| `max_tool_result_chars` | int | `16000` | Max characters in tool result |
| `provider_retry_mode` | `"standard"` \| `"persistent"` | `"standard"` | Retry behavior |
| `tool_hint_max_length` | int | `40` | Max chars for tool hint display |
| `reasoning_effort` | string \| null | `null` | LLM thinking effort (low/medium/high/adaptive/none) |
| `timezone` | string | `"UTC"` | IANA timezone |
| `bot_name` | string | `"agenite_claw"` | Display name in prompts |
| `bot_icon` | string | `"🐈"` | Icon next to bot name |
| `unified_session` | bool | `false` | Share one session across all channels |
| `disabled_skills` | list | `[]` | Skill names to exclude |
| `session_ttl_minutes` | int | `15` | Auto-compact idle threshold (0 = disabled) |
| `consolidation_ratio` | float | `0.5` | Target ratio after compression |
| `model_preset` | string \| null | `null` | Active preset name (overrides model/provider fields) |

### `agents.defaults.dream`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `true` | Register periodic Dream consolidation job |
| `interval_h` | int | `2` | Hours between consolidation runs |
| `cron` | string \| null | `null` | Legacy cron expression override |

## `channels`

Channel-specific configuration. Each enabled channel adds its own key under `channels`. See [channels.md](channels.md) for per-channel fields.

### Common fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `send_progress` | bool | `true` | Stream agent text progress to channel |
| `send_tool_hints` | bool | `false` | Stream tool-call hints |
| `show_reasoning` | bool | `true` | Surface model reasoning |
| `extract_document_text` | bool | `true` | Extract text from document attachments |
| `send_max_retries` | int | `3` | Max delivery attempts |

## `providers`

Provider-specific configuration. Each provider key maps to a `ProviderConfig`:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `api_key` | string \| null | `null` | API key (saved to vtx's dynamic_auth.json) |
| `api_base` | string \| null | `null` | Custom API base URL |
| `api_type` | `"auto"` \| `"chat_completions"` \| `"responses"` | `"auto"` | Request API surface |
| `extra_headers` | dict \| null | `null` | Custom HTTP headers |
| `extra_body` | dict \| null | `null` | Extra request body fields |
| `extra_query` | dict \| null | `null` | Extra query parameters |

Example:

```json
{
  "providers": {
    "openai": {
      "api_key": "sk-..."
    },
    "custom": {
      "api_key": "my-key",
      "api_base": "http://localhost:11434/v1"
    }
  }
}
```

## `tools`

Tool configuration.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `restrict_to_workspace` | bool | `false` | Keep tool access inside workspace |
| `webui_allow_local_service_access` | bool | `true` | Allow WebUI shell checks against localhost |
| `mcp_servers` | dict | `{}` | MCP server connections (see [mcp.md](mcp.md)) |
| `ssrf_whitelist` | list | `[]` | CIDR ranges exempt from SSRF blocking |

### `tools.web`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `search_provider` | string | `"duckduckgo"` | Default web search provider |
| `search_api_key` | string \| null | `null` | API key for search provider |

### `tools.exec`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `shell` | string \| null | `null` | Shell override (sh/bash/zsh) |
| `timeout_seconds` | int | `120` | Command timeout |
| `deny_patterns` | list | `[]` | Blocked command patterns |
| `allow_patterns` | list | `[]` | Allowed command patterns |

## `gateway`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `host` | string | `"127.0.0.1"` | Bind address |
| `port` | int | `18790` | Gateway port |
| `heartbeat.enabled` | bool | `true` | Enable heartbeat cron job |
| `heartbeat.interval_s` | int | `1800` | Heartbeat interval in seconds |
| `heartbeat.keep_recent_messages` | int | `8` | Messages to keep in heartbeat context |

## `api`

OpenAI-compatible API server (started via `agenite-claw serve`).

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `host` | string | `"127.0.0.1"` | Bind address |
| `port` | int | `8900` | API server port |
| `timeout` | float | `120.0` | Per-request timeout in seconds |

## `transcription`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `true` | Enable audio transcription |
| `provider` | string \| null | `null` | Transcription provider (see [audio.md](audio.md)) |
| `model` | string \| null | `null` | Model override |
| `language` | string \| null | `null` | Language code (2-3 chars) |
| `max_duration_sec` | int | `120` | Max audio duration |
| `max_upload_mb` | int | `25` | Max upload size |

## `model_presets`

Named model configurations for quick switching via `/model`:

```json
{
  "model_presets": {
    "fast": {
      "model": "anthropic/claude-sonnet-4-20250514",
      "provider": "anthropic",
      "max_tokens": 4096,
      "context_window_tokens": 200000,
      "temperature": 0.1
    },
    "creative": {
      "label": "Creative Writing",
      "model": "openai/gpt-4o",
      "temperature": 0.8,
      "reasoning_effort": "high"
    }
  }
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `label` | string \| null | `null` | Display name |
| `model` | string | required | Model identifier |
| `provider` | string | `"auto"` | Provider name |
| `max_tokens` | int | `8192` | Output token limit |
| `context_window_tokens` | int | `200000` | Context window size |
| `temperature` | float | `0.1` | Sampling temperature |
| `reasoning_effort` | string \| null | `null` | Thinking effort |

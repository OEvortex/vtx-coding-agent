# Configuration

Vtx stores all settings in a single YAML file generated automatically on first run:

```
~/.vtx/config.yml
```

The fully-commented default is available at `src/vtx/defaults/config.yml`. Migrations run automatically when `meta.config_version` is older than the current schema version (currently `12`).

## Schema

### `meta`

```yaml
meta:
  config_version: 12
```

### `llm`

```yaml
llm:
  default_provider: "openai-codex"     # provider slug
  default_model: "gpt-5.5"             # model id
  default_base_url: ""                 # override for local/compatible endpoints
  default_thinking_level: "low"        # none | minimal | low | medium | high | xhigh
  tool_call_idle_timeout_seconds: 180
  request_timeout_seconds: 600
  auth:
    openai_compat: "auto"              # auto | required | none
    anthropic_compat: "auto"
  tls:
    insecure_skip_verify: false        # true to trust self-signed local certs
  system_prompt:
    git_context: true                  # attach a git status snapshot to the system prompt
    content: ""                        # leave blank to use the built-in system prompt
```

### `compaction`

```yaml
compaction:
  on_overflow: "continue"              # continue (auto-compact) | pause
  threshold_percent: 80                # auto-compact at 80% of the context window
```

### `goal`

See [goal.md](goal.md). Controls the `/goal` continuous-work command.

```yaml
goal:
  enabled: true
  max_turns: 100
  max_objective_chars: 4000
  evaluator_provider: ""               # empty = active default
  evaluator_model: ""
```

### `agent`

```yaml
agent:
  max_turns: 500                       # global safety cap per session
  default_context_window: 200000
```

### `ui`

```yaml
ui:
  theme: "gruvbox-dark"                # see theming.md
  collapse_thinking: true
  thinking_lines: "1"                  # 1 | 2 | 3 | 4 | 5 | none
  colored_tool_badge: true
  show_welcome_shortcuts: true
  hidden_models: []                    # "provider" hides all its models; "provider:model" hides one
  model_provider_filter: ""            # empty = all; set a slug to scope the /model picker
```

### `permissions`

```yaml
permissions:
  mode: "prompt"                       # prompt | auto
```

### `notifications`

```yaml
notifications:
  enabled: false
  volume: 0.5                          # 0.0 – 1.0
```

### `extensions`

List of enabled extension module names under `~/.vtx/agent/extensions/`.

```yaml
extensions: []
```

### `agents`

Switchable handoff agent profiles. See [agents.md](agents.md).

```yaml
agents:
  default: ""
  switch_mode: "lock"                  # lock | unlock
  files: []
```

### `task`

Built-in sub-agent presets for the `task` tool. See [tools.md](tools.md).

```yaml
task:
  subagent_presets:
    - name: "general-purpose"
      description: "Balanced sub-agent for delegating well-scoped tasks."
      tools_allow: []
      max_turns: 200
    - name: "Explore"
      description: "Read-only repository exploration agent."
      instructions: "You are an exploration agent. You cannot modify the filesystem..."
      tools_allow: ["read", "find", "skill", "fetch_webpage", "web_search"]
      max_turns: 100
```

## Overriding the system prompt

Set `llm.system_prompt.content` to a custom string to fully replace the built-in prompt. Leave it blank to use the default minimalist prompt. `git_context` toggles whether a `git status`/`git diff` snapshot is attached to the system prompt at startup.

## Editing safely

Config changes take effect on the next session (or `/new`). Invalid values fall back to built-in defaults with a warning logged to stderr.

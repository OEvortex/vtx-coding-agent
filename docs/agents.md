# Switchable Handoff Agents

Vtx supports named, switchable agent profiles ("handoff agents"). Each profile is a focused system-prompt + tool surface + model configuration you can cycle between in the TUI with `Shift+Tab`. They are ideal for distinct modes like read-only review, security audit, or fast implementation.

## Locations & discovery

Profiles are Python files defining a module-level `AGENT` constant:

- Project: `<cwd>/.vtx/agent/<name>.py` (searched up to the git root; deeper wins)
- Global: `~/.vtx/agent/<name>.py`

Multiple profiles can be configured explicitly via `agents.files:` in `config.yml` or the `--agent-file` CLI flag. The default active profile is set with `agents.default:`; `agents.switch_mode` is `lock` or `unlock`.

See `examples/agents/` for working profiles (`code-review.py`, `security-audit.py`, `explorer.py`, `data-engineer.py`, `yolo.py`).

## Profile schema

```python
AGENT = {
    "name": "security-audit",            # required, [a-z0-9-]
    "description": "Read-only security review.",  # required
    "icon": "🛡",                          # optional (max 4 chars)
    "color": "red",                       # optional theme color

    # Model / provider overrides
    "model": "gpt-5.5",
    "provider": "openai-codex",
    "base_url": None,
    "thinking_level": "high",
    "max_turns": 100,

    # System prompt composition
    "instructions": "You are a security auditor...",
    "instructions_mode": "append",        # append | replace

    # Tool surface (built-in tool names, or tool-group names like "read-only"/"full")
    "tools_allow": ["read", "grep", "find"],
    "tools_deny": [],

    # Permissions
    "permission_mode": "prompt",          # prompt | auto
    "permission_gates": [],

    # Handoffs to other profiles
    "handoffs": ["code-review"],
    "handoff_back": True,

    # Agent-scoped extensions to load when active
    "extensions": [],
}
```

All fields are optional except `name` and `description`.

## Using profiles

- `Shift+Tab` in the TUI cycles through loaded profiles.
- `/handoff <query>` summarizes the current session and starts a fresh session under a chosen profile, carrying the summary forward.
- A profile's `instructions` is appended to (or replaces) the base system prompt per `instructions_mode`.
- A profile may `handoff` to other named profiles, letting the model route work between specialists.

## `task` sub-agents vs handoff agents

Handoff agents run in the **same** session and context. The `task` tool dispatches **separate** sub-agent sessions (see [tools.md](tools.md)). Use `task` for parallel, isolated work; use handoff agents for in-session persona switching.

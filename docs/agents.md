# Agents

A handoff **agent** is a named, switchable profile that bundles instructions,
tool allow/deny rules, optional model overrides, and agent-scoped tools,
slash commands, and permission gates. Agents are the natural unit of "I'm
in a different mode now" — read-only review, fast-and-loose implementation,
security audit, data-engineering profile, and so on.

Agents live in plain Python files at:

* `<cwd>/.vtx/agent/<name>.py` (project) — walked up to the git root
* `~/.vtx/agent/<name>.py` (global)

Project wins on name collision (same rule as skills and extensions). A
project file `code-review.py` exporting `AGENT = AgentDef(name="code-review", ...)`
is the canonical "I have a code-review profile for this repo" pattern.

The TUI ships a **Shift+Tab** keybinding that cycles through the
discovered agents (`[None, *names]`, alphabetical). The CLI exposes
`--agent NAME` to start with one active, and `/agent` opens a picker
when no name is given.

## Quick start

Create `.vtx/agent/code-review.py` in your project:

```python
from vtx.agents import AgentDef

AGENT = AgentDef(
    name="code-review",
    description="Read-only code review profile",
    icon="🔍",
    thinking_level="high",
    tools_allow=["read", "find", "grep", "skill", "ask_user"],
    tools_deny=["bash", "write", "edit"],
    permission_mode="auto",
    instructions=(
        "You are reviewing code, not writing it. Be terse. "
        "Output [P0]..[P3] findings only."
    ),
    instructions_mode="append",
)


def register(api):
    @api.local_tool(
        name="pr_summary",
        description="Summarize the current PR's diff",
        parameters={
            "type": "object",
            "properties": {"base": {"type": "string"}},
            "required": ["base"],
        },
        mutating=False,
    )
    def pr_summary(args, ctx):
        # ... call out to git, return a summary ...
        return {"success": True, "result": "..."}

    @api.local_command(name="checklist", description="Run the review checklist")
    def checklist(args):
        return "review checklist:\n  - [ ] tests"

    api.permission_gate(
        tool="bash",
        when="command matches 'sudo'",
        action="deny",
        reason="sudo blocked in review mode",
    )
```

Run it:

```bash
vtx --agent code-review            # start with the agent active
vtx                                  # start normally, then press Shift+Tab
/agent list                          # see all discovered agents
/agent code-review                   # switch to one
/agent off                           # back to the default session profile
```

## What an agent is

An `AgentDef` is the static profile:

| Field | Type | Purpose |
|---|---|---|
| `name` | `str` | required; matches the file stem; `^[a-z0-9-]+$` |
| `description` | `str` | required; shown in the `/agent` picker and TUI tooltip |
| `icon` | `str \| None` | optional; max 4 chars (emoji friendly); shown in the info bar |
| `color` | `str \| None` | optional theme color name for the tab badge |
| `model` | `str \| None` | optional model override for this agent |
| `provider` | `str \| None` | optional provider override |
| `base_url` | `str \| None` | optional base URL override (local models) |
| `thinking_level` | `ThinkingLevel \| None` | optional override |
| `max_turns` | `int \| None` | optional override of `agent.max_turns` |
| `instructions` | `str \| None` | optional; injected into the system prompt |
| `instructions_mode` | `"append" \| "replace"` | default `"append"`; `"replace"` swaps the base identity out |
| `tools_allow` | `list[str] \| None` | optional allowlist; `None` = all built-ins |
| `tools_deny` | `list[str]` | applied after `tools_allow`; default `[]` |
| `permission_mode` | `"auto" \| "prompt" \| None` | optional override of the global `permissions.mode` |
| `permission_gates` | `list[PermissionGate]` | declarative gates layered on top of `permissions.py` |
| `handoffs` | `list[str]` | other agent names this agent can route to |
| `handoff_back` | `bool` | default `True`; whether `transfer_to_default` is exposed |
| `extensions` | `list[str]` | agent-scoped extension paths loaded only when active |
| `metadata` | `dict[str, Any]` | free-form; surfaced in traces |

The full Pydantic model lives in `src/vtx/agents/schema.py`.

## File anatomy

A minimal agent file is **data only** — just a module-level `AGENT`:

```python
from vtx.agents import AgentDef

AGENT = AgentDef(name="yolo", description="Fast implementation mode",
                 permission_mode="auto", max_turns=1000)
```

For agents that ship their own tools, commands, gates, or lifecycle hooks,
add an optional `register(api)`:

```python
def register(api):
    @api.local_tool(name="...", description="...", parameters={...})
    def my_tool(args, ctx):
        return {"success": True, "result": "..."}
```

The `AGENT.name` must equal the file stem (or the package directory name).
A mismatch raises `AgentLoadError` at load time so the user sees the typo
at startup, not at runtime.

`register(api)` is sync. Async factories are not supported in v0.1.x and
raise `AgentLoadError("async register() is not supported")`.

## `AgentAPI` — the imperative surface

`register(api)` receives an `AgentAPI`. The methods:

| Method | Purpose |
|---|---|
| `api.local_tool(name, description, parameters, *, execute=..., mutating=True, label=None)` | Register a tool scoped to this agent. Supports both function-call and decorator forms. The decorator form (no `execute=`) treats the function below the decorator as the execute callback. |
| `api.local_command(name, description, handler=...)` | Register a slash command scoped to this agent. Same decorator / function-call split as `local_tool`. |
| `api.permission_gate(tool, *, when=..., action="allow"\|"deny"\|"prompt", reason=None)` | Layer a permission rule on top of the AgentDef's declarative `permission_gates`. `when` may be a small expression string or a Python predicate. |
| `api.on(event, handler)` | Subscribe to a lifecycle event. The handler is wired into the runtime's event bus when the agent is activated. |
| `api.on_agent_change(handler)` | Shortcut for `api.on("agent_changed", handler)`. |
| `api.notify(message, level="info")` | Print a user-facing line to stderr (the chat log surfaces it). |
| `api.cwd`, `api.config_dir`, `api.definition` | Read-only context. |

### `local_tool` — function call vs decorator

```python
# Function call (explicit execute)
api.local_tool(
    name="pr_summary",
    description="...",
    parameters={...},
    execute=lambda args, ctx: {"success": True, "result": "..."},
    mutating=False,
)

# Decorator (function below is the execute)
@api.local_tool(
    name="pr_summary",
    description="...",
    parameters={...},
    mutating=False,
)
def pr_summary(args, ctx):
    return {"success": True, "result": "..."}
```

The decorator form is detected by the absence of `execute=`. The function
below must accept `(args, ctx)` and return a dict or `ToolResult`.

### `local_command` — same shape

```python
@api.local_command(name="checklist", description="Run the checklist")
def checklist(args):
    return "checklist (args: " + args + ")"
```

The handler receives the argument string (everything after `/checklist`)
and may return a `CommandOutcome`, a string (treated as `output`), or
`None` (silently succeeded).

### `permission_gate` — when-clause mini-language

`when` accepts a small expression string **or** a Python predicate:

```python
# Substring match
api.permission_gate(
    tool="bash",
    when="command matches 'rm -rf'",
    action="deny",
    reason="destructive",
)

# Exact equality
api.permission_gate(
    tool="bash",
    when="command == 'rm -rf /'",
    action="deny",
)

# Python predicate (anything the mini-language can't express)
def _blocks_sudo(args):
    return "sudo" in (args.get("command") or "")

api.permission_gate(
    tool="bash",
    when=_blocks_sudo,
    action="deny",
    reason="sudo blocked in review mode",
)
```

The mini-language supports `<path> matches '<literal>'` and
`<path> == '<literal>'`, where `<path>` is a dotted path into the tool's
args dict. Unknown expressions raise `ValueError` at registration time.

## Discovery and resolution

`src/vtx/agents/discovery.py::find_agent_paths` resolves agent files in
this order (later wins on name collision — same rule as skills):

1. Project: walk from cwd to git root, load `<dir>/.vtx/agent/<name>.py`
   and `<dir>/.vtx/agent/<name>/__init__.py` (package form).
2. Global: `~/.vtx/agent/<name>.py` and `~/.vtx/agent/<name>/__init__.py`.
3. Explicit: `--agent-file PATH` (repeatable) and `agents.files:` in
   `~/.vtx/config.yml`.

The loader (`src/vtx/agents/loader.py`) imports the file, validates
`AGENT`, and calls `register(api)` if present. Errors are collected and
surfaced as launch warnings — one bad agent does not block the rest.

## The active tool set

When an agent is active, the tool surface is:

```
active_tools =
    built_in_tools (filtered by base defaults)
  + session_extensions.tools                    (ExtensionAPI.register_tool)
  + active_agent.definition.extensions[].tools (loaded per agent)
  + active_agent.local_tools                   (AgentAPI.local_tool)
  - active_agent.tools_deny
  intersect with active_agent.tools_allow (when non-empty)
```

The agent's own local tools are exempt from its `tools_allow` / `tools_deny`
filters — they were explicitly contributed by the agent, not pulled from
the base pool. This lets a profile ship a local tool alongside a
restrictive allow list.

The same formula applies to slash commands (session + agent-local
commands are merged into the runtime's command dict; agent-local
commands show the agent's name as the owner).

## Activation modes

The `agents.switch_mode` config field controls how Shift+Tab / `/agent` behaves:

| Mode | Behavior |
|---|---|
| `"lock"` (default) | The active agent is set at session start; switching via the TUI/CLI starts a fresh session JSONL, preserving the session-tree lineage. |
| `"hot"` | Switching re-renders the system prompt and tool list in place on the next turn. The model's view of the tool surface can change mid-conversation. |

The MVP ships `lock` only. `hot` is a follow-up.

## CLI

| Flag | Effect |
|---|---|
| `--agent NAME` | Activate `NAME` at session start. |
| `--agent-file PATH` | Load an additional agent file or package (repeatable). |
| `--no-agents` | Skip auto-discovered agents in `.vtx/agent/` and `~/.vtx/agent/`. |
| `--list-agents` | Print the discovered agents and exit. |
| `VTX_AGENT=NAME` | Env-var equivalent of `--agent`. |

The active agent is persisted to `last_selected.agent` in
`~/.vtx/config.yml`, so a subsequent `vtx` (with no flag) resumes with
the same agent active.

## TUI

| Action | Effect |
|---|---|
| **Shift+Tab** | Cycle to the next agent (alphabetical). Shows a `→ agent: <name>` toast. |
| `/agent` | Open the agent picker. |
| `/agent list` | Render the agent table. |
| `/agent current` | Print the active agent's name and description. |
| `/agent <name>` | Switch to the named agent. |
| `/agent off` | Deactivate (back to the default session profile). |
| `/agent reload` | Re-read the agent files from disk. |

The active agent is shown in the bottom info bar as `@ <name>` (next to
the model and thinking level). Permission-mode cycling moved from
Shift+Tab to `Alt+Ctrl+P` to make room for agent cycling. `Ctrl+Shift+P`
is intentionally left free for a future command palette.

## Events

The agent system adds two new events to the extension bus:

| Event | Fires when... | Payload |
|---|---|---|
| `agent_activated` | An agent becomes active (session start, `/agent <name>`, Shift+Tab) | `agent: str` |
| `agent_changed` | The active agent changes | `previous: str \| None`, `current: str \| None` |

Both are in `ALL_EVENTS` and any extension can subscribe. The constants
live in `vtx.agents` (re-exported from `vtx.extensions`):

```python
from vtx.extensions import AGENT_ACTIVATED, AGENT_CHANGED
```

## Configuration

See [configuration.md](configuration.md#agents) for the full
`agents:` schema (`default`, `switch_mode`, `files`).

The config version was bumped 8 → 9 with a no-op migration that
adds `agents: { default: "", switch_mode: "lock", files: [] }` and
`last_selected.agent: null` to existing configs. The migration is
silent and a backup is written next to `config.yml`.

## Cross-agent tools from a regular extension

`ExtensionAPI` gains `register_local_tool(agent=..., ...)` for
extensions that aren't themselves agent files but want to contribute a
tool scoped to a specific agent. The runtime merges these into the
same per-agent bucket used by `AgentAPI.local_tool`:

```python
# ~/.vtx/agent_extensions/secrets_scan.py
from vtx.extensions import register_local_tool


def register(api):
    @api.register_local_tool(
        agent="security-audit",
        name="secrets_scan",
        description="Scan a path for hardcoded secrets",
        parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        mutating=False,
    )
    def secrets_scan(args, ctx):
        return {"success": True, "result": f"no secrets in {args['path']}"}
```

When the user activates the `security-audit` agent, the `secrets_scan`
tool appears in the model's tool list. When they switch out, it
disappears.

## Examples

- `examples/agents/code-review.py` — read-only profile with `pr_summary` (real `git diff --stat` call), `checklist` command, two `permission_gate`s, and an `agent_activated` lifecycle hook.
- `examples/agents/yolo.py` — data-only, no `register()`, just instructions + `permission_mode: auto`.
- `examples/agents/security-audit.py` — pulls in an agent-scoped extension via `extensions: [./security_extensions.py]`.
- `examples/agents/security_extensions.py` — the cross-agent `register_local_tool(agent=...)` pattern.
- `.vtx/agent/code-review.py` in this repo — a working demo.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Agent doesn't appear in `/agent` | `AGENT.name` doesn't match the file stem | Rename the file or fix the constant |
| `AgentLoadError: AGENT.name=... does not match file/package name` | Same as above | Either name should match the other |
| `AgentLoadError: does not export a top-level AGENT` | File lacks `AGENT = AgentDef(...)` | Add the constant |
| Tool not visible after switching agents | The agent's `tools_deny` is dropping it (or `tools_allow` is too restrictive) | Check `compose_active_tools` output with `python -c "from vtx.agents import ..."` |
| `bypass` of agent's own allow/deny for local tools | That's by design — see [The active tool set](#the-active-tool-set) | If you want a local tool to obey allow/deny, register it as a regular extension tool and rely on session-level enforcement |
| Agent registered but Shift+Tab is a no-op | No agents in the registry | Create at least one `.vtx/agent/<name>.py` |

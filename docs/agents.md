# Handoff agents

Agents are switchable profiles: each one bundles instructions, tool allow/deny lists, an optional model/provider override, and permission gates. Cycle them live with `shift+tab` or `/agent <name>`. Implemented in `src/ai/agent/agents/`.

## Defining an agent

A Python file in `.vtx/agent/<name>.py` (project, walked up to the git root) or `~/.vtx/agent/<name>.py` (global):

```python
# .vtx/agent/code-review.py
def setup(api):
    api.on_agent_change(lambda: api.notify("review mode"))

# metadata comes from a module-level AGENT dict or the setup() registration;
# the schema below is what vtx reads.
```

The schema (`AgentDef`, pydantic):

| Field | Default | Notes |
| --- | --- | --- |
| `name` | required | lowercase-hyphen, ≤ 64 chars |
| `description` | required | shown in `/agent` list and to the model |
| `icon`, `color` | none | TUI badge decoration |
| `model`, `provider`, `base_url` | parent's | per-agent model routing |
| `thinking_level` | parent's | `none`…`xhigh` |
| `max_turns` | unlimited | turn budget |
| `instructions` | none | extra system-prompt text |
| `instructions_mode` | `"append"` | or `"replace"` |
| `tools_allow`, `tools_deny` | all tools | surface filter; deny subtracts from allow |
| `tool_groups`, `active_tool_group` | none | named tool subsets cycled with `alt+ctrl+g` |
| `permission_mode` | config default | `auto` / `prompt` for this agent |
| `permission_gates` | `[]` | `{tool, when, action, reason}` rules |
| `handoffs` | `[]` | agents this one may hand off to |
| `handoff_back` | `true` | allow returning to the previous agent |
| `extensions` | `[]` | extensions scoped to this agent |
| `local_tools`, `local_commands` | — | registered via `api.local_tool()` / `api.local_command()` |

## Switching

- `shift+tab` cycles through discovered agents (`/agent` lists them).
- `alt+ctrl+g` cycles the active agent's tool groups.
- Start directly: `vtx --agent code-review`, or set `agents.default` / the `VTX_AGENT` env var.

Switch behaviour is controlled by `agents.switch_mode`: `lock` (default) starts a new lineage-linked session; `hot` re-renders prompt + tools in place next turn.

## Agent API

`setup(api)` receives an `AgentAPI`:

```python
api.local_tool("deploy", "Deploy a service", {...schema...}, execute=fn)
api.local_command("stage", "Stage the current branch", handler=fn)
api.permission_gate("bash", when="push" in cmd, action="deny", reason="...")
api.on("turn_start", handler)
api.on_agent_change(handler)
api.notify("message", level="warning")
```

Local tools/commands appear only while that agent is active. A user-defined agent always beats a built-in sub-agent preset of the same name.

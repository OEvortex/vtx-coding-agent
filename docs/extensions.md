# Extensions & hooks

Extensions are Python files that hook into Vtx at startup: register tools and slash commands, intercept lifecycle events, or gate tool calls. Implemented in `src/ai/agent/extensions.py`; the extension manager in `extension_manager.py`.

## Discovery

Loaded in this order (project wins on collision):

1. `<cwd>/.vtx/extensions/*.py` (walked up to the git root)
2. `~/.vtx/agent/extensions/*.py`
3. `extensions:` entries in config
4. `--extension/-e PATH` CLI flags

`--no-extensions` skips auto-discovery. Installed packages (below) land in the same global directory.

## Writing an extension

```python
# .vtx/extensions/audit.py
def setup(api):
    @api.on("tool_call")
    def audit(event):
        print("tool:", event["name"])

    api.register_command(
        "audit",
        "Toggle audit logging",
        handler=lambda args: "audit on",
    )

    api.register_tool(
        "hello",
        "Say hello",
        {"name": {"type": "string", "description": "Who"}},
        execute=lambda params, ctx=None: f"hi {params.name}",
        mutating=False,           # non-mutating tools skip the permission gate
    )
```

The `ExtensionAPI`:

| Method | Does |
| --- | --- |
| `on(event, handler)` | Subscribe to a lifecycle event; return `{"block": True, "reason": ...}` from `tool_call`/`tool_result` handlers to block or rewrite |
| `register_tool(name, description, parameters, execute=..., mutating=..., label=...)` | Add an LLM-callable tool (JSON schema → pydantic) |
| `register_local_tool(agent, ...)` | Same, but scoped to one handoff agent |
| `register_command(name, description, handler)` | Add a `/slash` command; return a string or `CommandOutcome(output, success, exit_after)` |
| `notify(message, level)` | Surface info/warning/error to the user |

## Lifecycle events

`session_start`, `session_end`, `agent_start`, `agent_end`, `turn_start`, `turn_end`, `tool_call`, `tool_result`, `compaction_start`, `compaction_end`, `agent_activated`, `agent_changed`, `tool_group_changed`.

`tool_call` and `tool_result` are blocking events: handlers run before the action completes and may veto it.

## YAML hooks

Prefer declarative? `.vtx/hooks.yml` registers shell/HTTP handlers without Python:

```yaml
PostToolUse:
  - matcher: bash
    type: command
    command: ./scripts/log-tool.sh
    timeout: 10

PreToolUse:
  - matcher: write
    type: http
    url: https://ci.internal/veto
    once: false
```

Hook fields: `event`, `matcher` (tool name glob), `type` (`command` | `prompt` | `http` | `agent`), `command` / `url` / `prompt_text` / `agent_instructions`, `timeout`, `once`, `if_condition`, `enabled`. A non-zero exit or `blocking_error` in the response vetoes the action.

Event names (30): `UserPromptSubmit`, `SessionStart`, `SessionEnd`, `TurnStart`, `TurnEnd`, `PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `PermissionRequest`, `PermissionDenied`, `SubagentStart`, `SubagentStop`, `Stop`, `StopFailure`, `PreCompact`, `PostCompact`, `Notification`, `PostSampling`, `Setup`, `InstructionsLoaded`, `CwdChanged`, `FileChanged`, `WorktreeCreate`, `WorktreeRemove`, `ConfigChange`, `TaskCreated`, `TaskCompleted`, `TeammateIdle`, `Elicitation`, `ElicitationResult`.

Python code can also subclass `AgentHook` (`before_run`, `after_iteration`, `on_stream`, `finalize_content`, …) and pass instances via the runtime — that's what the SDK uses for tracing.

## Extension manager

```bash
vtx install <name>            # tries vtx-<name> then <name> on PyPI; GitHub URLs work too
vtx install <name> --upgrade
vtx uninstall <name>
vtx list-extensions
```

PyPI installs go through `uv pip` into the active environment; GitHub sources are cloned. The ledger is `~/.vtx/installed_extensions.yml`.

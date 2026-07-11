# Extensions

Extensions let you customize Vtx with Python: add tools, intercept tool calls, register slash commands, and react to lifecycle events. They run in-process with the same privileges as the Vtx process — only load extensions you trust.

## Discovery

Extensions are discovered from (later wins on name conflict):

1. Project-local `.vtx/extensions/*.py` (and `*/__init__.py` packages)
2. Global `~/.vtx/agent/extensions/*.py`
3. The `extensions:` list in `config.yml`
4. `--extension PATH` CLI flag (repeatable)

Set `--no-extensions` to skip auto-discovery (only explicit `--extension` paths load). Vtx ships **no bundled extensions** — every extension is third-party-style. Enable the `task` sub-agent dispatcher by copying `examples/extensions/task_tool.py` into your extensions directory.

## Writing an extension

An extension is a single `.py` file (or package) that exports a top-level `register(api)` function:

```python
def register(api):
    # Subscribe to lifecycle events
    @api.on("tool_call")
    def guard(event, payload):
        if payload["name"] == "bash" and "rm -rf" in payload["args"].get("command", ""):
            return {"block": True, "reason": "rm -rf is not allowed"}
        return None

    # Add a new tool
    api.register_tool(
        name="weather",
        description="Get the current weather for a city.",
        parameters={
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "City name"}
            },
            "required": ["city"],
        },
        execute=my_weather_fn,
        mutating=False,
    )

    # Add a slash command
    @api.on  # placeholder; use register_command below
    def _ignored():
        pass

    api.register_command("hello", "Say hello", lambda args: f"Hello, {args or 'world'}!")
```

### Tool `execute` signature

```python
def my_tool(args: dict, ctx: dict | None) -> ToolResult | dict | str | None:
    ...
```

May be sync or async. Return a `ToolResult`-like dict (`{"success": True, "result": "..."}`) or a string. Tool parameters are a JSON Schema object; Vtx synthesizes a pydantic model for validation. Set `mutating=True/False` to control whether the permission gate applies.

### Commands

`api.register_command(name, description, handler)` registers `/name`. The `handler` receives the argument string and returns a `CommandOutcome`, a string, or `None`.

## Events

Subscribe with `api.on(event, handler)` or the `@api.on(event)` decorator. Handlers may be sync or async; exceptions are logged and never crash the loop.

| Event | Blocking | Payload / effect |
|-------|----------|------------------|
| `session_start`, `session_end` | no | session lifecycle |
| `agent_start`, `agent_end` | no | agent run lifecycle |
| `turn_start`, `turn_end` | no | per-turn lifecycle |
| `tool_call` | **yes** | return `{"block": True, "reason": "..."}` to deny, or `{"args": {...}}` to rewrite args |
| `tool_result` | **yes** | return `{"output": "..."}` to replace the text the model sees |
| `compaction_start`, `compaction_end` | no | context compaction |
| `agent_activated`, `agent_changed`, `tool_group_changed` | no | agent switching |
| `goal_start`, `goal_end`, `goal_paused`, `goal_resumed` | no | goal mode (see [goal.md](goal.md)) |

Blocking handlers must return a dict to take effect. The first `tool_call` handler that returns `block: True` short-circuits.

## Notifications

`api.notify(message, level="info"|"warning"|"error")` logs an extension message (surfaced via stderr in TUI and headless modes).

# Hooks

Vtx does not have a separate hook config file. The hook lifecycle is delivered through the **extension event bus** — you subscribe to lifecycle and tool events from an extension's `register(api)` function. See [extensions.md](extensions.md) for the full API.

## Hook points

| Hook point | Event | Blocking | Use it to… |
|------------|-------|----------|-----------|
| Before a tool runs | `tool_call` | **yes** | deny (`{"block": True, "reason": ...}`) or rewrite args (`{"args": {...}}`) |
| After a tool runs | `tool_result` | **yes** | replace the output the model sees (`{"output": "..."}`) |
| Session start / end | `session_start`, `session_end` | no | set up / tear down state |
| Agent run start / end | `agent_start`, `agent_end` | no | profile-specific setup |
| Turn start / end | `turn_start`, `turn_end` | no | per-turn instrumentation |
| Context compaction | `compaction_start`, `compaction_end` | no | snapshot or notify |
| Agent switch | `agent_activated`, `agent_changed`, `tool_group_changed` | no | react to persona changes |
| Goal mode | `goal_start`, `goal_end`, `goal_paused`, `goal_resumed` | no | observe goal transitions |

## Example: a pre-tool hook

```python
def register(api):
    @api.on("tool_call")
    def deny_dangerous_bash(event, payload):
        if payload["name"] == "bash":
            cmd = payload["args"].get("command", "")
            if cmd.strip().startswith("rm -rf"):
                return {"block": True, "reason": "rm -rf is disabled by policy"}
        return None
```

Blocking handlers (`tool_call`, `tool_result`) must return a dict to take effect. The first `tool_call` handler that returns `block: True` short-circuits the call. Handlers may be sync or async; exceptions are logged and never crash the agent loop.

# Hooks

Vtx's hook system lets you react to session, tool, permission, and lifecycle
events through YAML configuration or programmatic registration. Hooks are
loaded from `.vtx/hooks.yml` (project) or `~/.vtx/hooks.yml` (global) and
bridged onto the extension EventBus so they fire alongside extension handlers.

## Quick start

Create `.vtx/hooks.yml` in your project root:

```yaml
# .vtx/hooks.yml
setup:
  - event: SessionStart
    type: command
    command: echo "Session started at $(date)"

pre_tool:
  - event: PreToolUse
    type: command
    command: echo "About to use tool"
    matcher: "bash*"
```

That's it — vtx loads the file automatically at startup and fires matching
hooks when the events occur.

## Hook config

Each hook entry has these fields:

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `event` | string | yes | — | One of the supported hook events (see table below) |
| `type` | string | no | `command` | `command`, `prompt`, `http`, or `agent` |
| `command` | string | if `type: command` | `""` | Shell command to execute |
| `matcher` | string | no | `null` | Tool-name filter for tool hooks (`bash*`, `read`, `*edit`) |
| `once` | bool | no | `false` | Run only the first time the hook matches |
| `enabled` | bool | no | `true` | Set to `false` to disable a hook without removing it |
| `timeout` | int | no | `30` | Max seconds for command hooks |
| `if_condition` | string | no | `null` | Guard expression — hook only fires if truthy |

## Hook events

| Category | Events |
|---|---|
| Session lifecycle | `SessionStart`, `SessionEnd`, `Setup`, `InstructionsLoaded` |
| Turn lifecycle | `TurnStart`, `TurnEnd` |
| Tool execution | `PreToolUse`, `PostToolUse`, `PostToolUseFailure` |
| Permissions | `PermissionRequest`, `PermissionDenied` |
| Subagents | `SubagentStart`, `SubagentStop` |
| Stop handling | `Stop`, `StopFailure` |
| Compaction | `PreCompact`, `PostCompact` |
| Notifications | `Notification`, `PostSampling` |
| Workspace changes | `CwdChanged`, `FileChanged`, `WorktreeCreate`, `WorktreeRemove` |
| Runtime state | `ConfigChange`, `TaskCreated`, `TaskCompleted`, `TeammateIdle` |
| Elicitation | `Elicitation`, `ElicitationResult` |

## Event mapping

The bridge maps extension EventBus events to hook events:

| Extension event | Hook event(s) | Blocking? |
|---|---|---|
| `session_start` | `SessionStart` | no |
| `session_end` | `SessionEnd` | no |
| `turn_start` | `TurnStart` | no |
| `turn_end` | `TurnEnd` | no |
| `tool_call` | `PreToolUse` | **yes** — can deny tool execution |
| `tool_result` | `PostToolUse` | **yes** — can deny the tool result |
| `compaction_start` | `PreCompact` | no |
| `compaction_end` | `PostCompact` | no |

## Blocking hooks

`PreToolUse` and `PostToolUse` are **blocking** — if the hook command exits
with a non-zero code, the action is denied. For `PreToolUse` this prevents
the tool from executing. For `PostToolUse` this prevents the tool result
from being sent to the LLM.

### Block dangerous bash commands

```yaml
pre_tool:
  - event: PreToolUse
    type: command
    command: |
      # Exit 0 = allow, exit 1 = block with stderr as the reason
      echo "Blocked: use the edit tool instead of bash" >&2
      exit 1
    matcher: "bash*"
```

### Allow all writes

```yaml
pre_tool:
  - event: PreToolUse
    type: command
    command: exit 0
    matcher: "write*"
```

### Guard with a matcher

Only fire for the `bash` tool (exact match):

```yaml
pre_tool:
  - event: PreToolUse
    type: command
    command: echo "bash called"
    matcher: "bash"
```

## Matcher patterns

The `matcher` field filters which tool calls trigger the hook:

| Pattern | Matches |
|---|---|
| `bash` | Only the `bash` tool |
| `bash*` | `bash`, `bash_tool`, etc. (prefix match) |
| `*edit` | `edit`, `file_edit`, etc. (suffix match) |
| `read` | Only `read` |
| (omitted) | All tools |

## YAML structure

The YAML file uses **category keys** (arbitrary labels) containing lists of
hook entries. The category key is for readability only — the `event` field
inside each entry determines when the hook fires:

```yaml
# Category keys are just for organization
session_hooks:
  - event: SessionStart
    type: command
    command: echo "hello"

tool_hooks:
  - event: PreToolUse
    type: command
    command: echo "checking tool"
    matcher: "bash*"

  - event: PostToolUse
    type: command
    command: echo "tool done"
```

## Complete examples

### Log all tool calls

```yaml
logging:
  - event: PreToolUse
    type: command
    command: echo "$(date): PreToolUse" >> /tmp/vtx-hook-log.txt

  - event: PostToolUse
    type: command
    command: echo "$(date): PostToolUse" >> /tmp/vtx-hook-log.txt
```

### Run linter after file edits

```yaml
post_edit:
  - event: PostToolUse
    type: command
    command: |
      if command -v ruff &>/dev/null; then
        ruff check --fix .
      fi
    matcher: "edit*"
```

### Notification on session end

```yaml
notifications:
  - event: SessionEnd
    type: command
    command: notify-send "VTX" "Session ended"
```

### Block tool on first use only

```yaml
one_time_guard:
  - event: PreToolUse
    type: command
    command: |
      echo "First use blocked — restart to allow" >&2
      exit 1
    matcher: "bash*"
    once: true
```

### Disable a hook temporarily

```yaml
disabled_hook:
  - event: PreToolUse
    type: command
    command: echo "this is disabled"
    enabled: false
```

## Programmatic API

For extensions or in-process use, register hooks directly with Python
handler callbacks. The handler receives a context dict and the hook config:

```python
from vtx.hooks import HookConfig, HookRegistry, HookResult, HookRuntime

registry = HookRegistry()

async def on_start(context, config):
    # context has: event, session_id, cwd, timestamp
    return HookResult(output="started")

await registry.register(
    "SessionStart",
    HookConfig(event="SessionStart", command="echo start"),
    handler=on_start,
)

runtime = HookRuntime(registry)
await runtime.emit("SessionStart", {"event": "SessionStart", "session_id": "abc"})
```

### Using the bridge

The `HookBridge` connects the hook system to the extension EventBus:

```python
from pathlib import Path
from vtx.hooks import HookBridge

bridge = HookBridge(
    bus=event_bus,
    project_path=Path(".vtx/hooks.yml"),
    global_path=Path.home() / ".vtx" / "hooks.yml",
)
await bridge.load()
# Hooks are now active — they fire when EventBus events occur
await bridge.unload()
```

## Hook context (programmatic handlers)

Programmatic Python handler callbacks receive a context dict with:

| Field | Available on | Description |
|---|---|---|
| `event` | all | The hook event name |
| `session_id` | session events | Current session ID |
| `cwd` | session events | Working directory |
| `timestamp` | all | Unix timestamp |
| `tool_name` | tool events | Name of the tool |
| `arguments` | tool events | Tool arguments dict |
| `tool_call_id` | tool events | Unique tool call ID |
| `result` | PostToolUse | Tool result (if available) |
| `permission` | permission events | Permission type requested |
| `tokens_before` | compaction events | Token count before compaction |
| `tokens_after` | compaction events | Token count after compaction |

Shell command hooks (`type: command`) do not receive this dict. They run as
standalone shell processes and communicate results via exit code (0 = allow,
non-zero = block with stderr as the reason message).

## Sources and precedence

Hooks are loaded from two locations and merged:

1. `~/.vtx/hooks.yml` (global/user) — loaded first
2. `.vtx/hooks.yml` (project-local) — loaded second, prepended

Project-local hooks appear **before** global hooks in the execution order
for the same event. Both sets fire when their conditions match.

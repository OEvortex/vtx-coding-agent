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

## Interactive UI (`ctx.ui`)

Handlers declared with **three** parameters — `(event, payload, ctx)` — also
receive a context whose `ctx.ui` exposes interactive dialogs and prompts.
Two-argument handlers keep working unchanged.

```python
def setup(api):
    @api.on("tool_call")
    async def gate(event, payload, ctx):
        if payload["name"] == "bash" and "rm -rf" in str(payload.get("args", {})):
            if not await ctx.ui.confirm("Dangerous", "Allow rm -rf?"):
                return {"block": True, "reason": "denied by user"}

    @api.on("session_start")
    async def pick(event, payload, ctx):
        theme = await ctx.ui.select("Theme", ["dark", "light"])
        name = await ctx.ui.input("Your name", placeholder="who")
        ctx.ui.notify(f"hi {name}", "info")
        ctx.ui.setStatus("theme", theme or "default")   # footer status line
        ctx.ui.setWidget("todo", ["- one", "- two"])    # persistent widget bar
```

| Method | Does |
| --- | --- |
| `await confirm(title, message=None, *, timeout=None, signal=None)` | Yes/no modal dialog (`y`/`n`/Enter/Esc); returns `bool` |
| `await select(title, options, *, timeout=None, signal=None)` | Pick-one modal list; returns the choice or `None` |
| `await input(title, placeholder="", *, default=None, timeout=None, signal=None)` | Free-text modal prompt; returns the string or `None` |
| `await custom(component, *, timeout=None, signal=None)` | Show a custom Textual widget/screen modally; returns whatever it dismisses with |
| `notify(message, level="info")` | Styled line in the chat log |
| `setStatus(key, value)` / `setWidget(key, lines)` | Persistent status/widget bar at the bottom of the screen (`None` clears) |

Dialog primitives are coroutines so the same code runs in every mode: in TUI
mode they show real modal dialogs; in headless/print mode they resolve
immediately to safe defaults (`False` / `None`). `timeout` (seconds)
dismisses the dialog and returns the default; `signal` is an
`asyncio.Event`-like abort handle. Handlers that use them must be
`async def`.

### Custom components

`await ctx.ui.custom(component)` shows arbitrary Textual UI modally —
pass a widget instance, widget class, `ModalScreen` subclass/instance, or
zero-arg callable returning either. Screens are pushed as-is; plain widgets
are wrapped in a bordered modal (`ExtensionCustomScreen`, Escape → `None`).
Return a value from the component with `self.screen.dismiss(result)`.

```python
from textual.containers import Vertical
from textual.widgets import Button, Label, Static

class ColorPicker(Static):
    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.screen.dismiss(event.button.label)  # value returned to the handler

def setup(api):
    @api.on("session_start")
    async def pick_color(event, payload, ctx):
        color = await ctx.ui.custom(ColorPicker("Pick a theme color"))
        if color:
            ctx.ui.notify(f"using {color}")
```

## Lifecycle events

`session_start`, `session_end`, `agent_start`, `agent_end`, `turn_start`, `turn_end`, `tool_call`, `tool_result`, `compaction_start`, `compaction_end`, `agent_activated`, `agent_changed`, `tool_group_changed`.

`tool_call` and `tool_result` are blocking events: handlers run before the action completes and may veto it.

## Provider request hooks

Two events let you inspect and rewrite every outgoing LLM request —
useful for gateway tracing headers, custom auth injection, or forcing
request parameters:

```python
def setup(api):
    @api.on_before_provider_headers
    def trace(event, payload):
        payload["headers"]["x-session-id"] = "my-trace-id"
        # payload["headers"]["X-Something"] = None  # deletes a header

    @api.on_before_provider_request
    def force_temp(event, payload, ctx):
        return {"payload": {**payload["payload"], "temperature": 0}}
```

| Event | Payload | Return |
| --- | --- | --- |
| `before_provider_headers` | `provider`, `model`, `headers` (mutate in place; set a key to `None` to delete) | ignored |
| `before_provider_request` | `provider`, `model`, `payload` (full wire payload; mutate in place) | `{"payload": {...}}` to replace |

Both fire once per request after the payload is fully built and before it
is sent, across all transports (OpenAI SDK, Anthropic HTTP).
Retries reuse the prepared values — handlers are not re-fired. Handlers
run in load order and later ones chain off earlier replacements.

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

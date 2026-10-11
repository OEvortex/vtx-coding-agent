# Extensions & hooks

An extension is a **plugin**. A Python file that exposes `register(api)` is
discovered before the mount pass and mounted as its own row of the running
composition, so its tools, its slash commands and its event handlers land in the
same registries every built-in row uses. There is no second extension system
behind it — [plugins.md](plugins.md) is the contract, this page is the authoring
surface.

An extension loads in-process with the user's own permissions. That is
deliberate and unchanged, and it is the reason a plugin you install is a plugin
you must trust.

## Discovery

Loaded in this order (project wins on collision):

1. `<cwd>/.vtx/extensions/*.py` (and `*/__init__.py`)
2. `~/.vtx/agent/extensions/*.py`
3. `extensions:` entries in config
4. `--extension/-e PATH` CLI flags
5. entry points a package declares, under the `vtx.extensions` group or the older
   `…agent.extensions` group

Each discovered file becomes a row named `extension:<stem>`, appended to the
profile's rows before they mount, so its teardown is the host's teardown.
`vtx --dump-config` prints the rows the profile declares; the extension rows
themselves are discovered at boot, so `vtx --list-extensions` is what lists them.
`--no-extensions` skips auto-discovery; only the paths you passed explicitly
still load.

A module that raises during import or during `register` is recorded as an error
rather than raised: its half-built record is discarded, the other rows still
mount, and the failure is named on stderr.

## Writing an extension

```python
# .vtx/extensions/audit.py
def register(api):
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

The `ExtensionAPI` is a thin view onto the composition's `ctx`, so everything
registered through it is a reversible effect owned by the row:

| Method | Does |
| --- | --- |
| `on(event, handler)` | Subscribe to a lifecycle event on the composition's `EventHub`; return `{"block": True, "reason": ...}` from a blocking handler to stop the work, or `{"args": {...}}` / `{"output": ...}` to rewrite it |
| `register_tool(name, description, parameters, execute=..., mutating=..., label=...)` | Add an LLM-callable tool (JSON schema → pydantic); registering a built-in's name replaces it |
| `register_local_tool(agent, name, ...)` | Same, but scoped to one handoff agent |
| `register_command(name, description, handler)` | Add a `/slash` command on `ctx.commands`; return a string or `CommandOutcome(output, success, exit_after)` |
| `notify(message, level)` | Surface info/warning/error through `ctx.ui` |
| `ui` | The interactive surface described below, or `None` when no TUI is installed |
| `on_session_start(...)` and the other `on_*` shorthands | `on(<event>, handler)` with the event filled in |
| `register_shortcut(...)`, `register_flag(...)` | Keyboard shortcuts and config flags |
| `register_provider(name, config)` | Queue a provider the runtime adopts on the next reload |
| `send_message(...)`, `append_entry(...)`, `set_session_name(...)`, `exec(...)` | Act on the live session |

## Interactive UI (`ctx.ui`)

Handlers declared with **three** parameters — `(event, payload, ctx)` — also
receive the extension API, whose `ctx.ui` exposes interactive dialogs and
prompts. Two-argument handlers keep working unchanged.

```python
def register(api):
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


def register(api):
    @api.on("session_start")
    async def pick_color(event, payload, ctx):
        color = await ctx.ui.custom(ColorPicker("Pick a theme color"))
        if color:
            ctx.ui.notify(f"using {color}")
```

## Lifecycle events

| Group | Events |
| --- | --- |
| Session | `session_start`, `session_end`, `session_info_changed`, `session_before_switch`, `session_before_fork`, `session_before_tree`, `session_tree`, `session_before_compact`, `session_compact`, `session_compact_failed`, `session_shutdown` |
| Agent | `agent_start`, `agent_end`, `agent_settled`, `before_agent_start`, `agent_activated`, `agent_changed`, `tool_group_changed` |
| Turn and message | `turn_start`, `turn_end`, `message_start`, `message_update`, `message_end` |
| Tool | `tool_call`, `tool_result`, `tool_execution_start`, `tool_execution_update`, `tool_execution_end` |
| Permission | `permission_request`, `permission_denied` |
| Compaction | `compaction_start`, `compaction_end` |
| Provider | `before_provider_headers`, `before_provider_request`, `after_provider_response` |
| Input and selection | `input`, `user_bash`, `model_select`, `thinking_level_select` |
| Context | `context`, `project_trust`, `resources_discover` |

`vtx.plugins.event_contract.ALL_EVENTS` is the authoritative list.

`tool_call`, `tool_result`, `user_bash` and `input` are blocking events:
handlers run before the action completes and may veto or rewrite it. Each event
name has exactly one dispatch mode on the hub, declared once by the kernel; see
the mode table in [plugins.md](plugins.md).

### Retired: refinement events

Refinement and the continual harness it wrote to were removed with the RLM
mode. `session_before_refine` and `refine_complete` are no longer emitted; the
name constants still exist, so an extension that subscribes to them loads without
error and simply never fires.

## Provider request hooks

Two events let you inspect and rewrite every outgoing LLM request —
useful for gateway tracing headers, custom auth injection, or forcing
request parameters:

```python
def register(api):
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
is sent, across all transports (OpenAI SDK, Anthropic HTTP). The extensions row
installs the bridge from the composition's event hub to the transport registry;
with no composition mounted the transport registry is untouched.
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

Event names (11): `SessionStart`, `SessionEnd`, `TurnStart`, `TurnEnd`, `PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `PermissionRequest`, `PermissionDenied`, `PreCompact`, `PostCompact`.

Python code can also subclass `AgentHook` (`before_run`, `after_iteration`, `on_stream`, `finalize_content`, …) and pass instances via the runtime — that's what the SDK uses for tracing.

Loading `hooks.yml` is itself a row (`hooks`), so a profile can disable or
replace it like anything else. The `AgentHook` protocol and the bridge the TUI
and the headless runner use live in `vtx.agent.hooks`.

## Installing

```sh
vtx plugin install <spec>       # PyPI name or git URL, into the active environment
```

Installing is gated on the plugin's declared vtx version range; an
incompatible plugin refuses and prints the command that grants an exemption for
exactly that pair. `vtx --list-extensions` prints the extension rows the running
composition mounted, and `/reload` re-discovers in place — unloading the
previous pass first, so a removed extension's tools and handlers are actually
gone rather than shadowed.

See the install and compatibility sections of [plugins.md](plugins.md).

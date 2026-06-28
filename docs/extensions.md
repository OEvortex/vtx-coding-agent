# Extensions

Vtx ships with a built-in extension system so you can add new tools,
intercept tool calls, react to lifecycle events, and register new
slash commands without forking the codebase. This page is a quick
start; see the module docstring in `vtx/extensions.py` for the full
API.

## Quick start

Create a file at `~/.vtx/agent/extensions/hello.py` (or
`.vtx/extensions/hello.py` in a project):

```python
from vtx.extensions import AGENT_START, AGENT_END, SESSION_START


def register(api):
    @api.on(SESSION_START)
    def on_start(event, payload):
        api.notify("hello extension loaded")

    @api.on(AGENT_END)
    def on_done(event, payload):
        api.notify(f"agent ended: {payload.get('stop_reason')}")
```

Drop in the file, restart vtx, and you'll see the notifications. The
extension is a plain Python module — no compilation, no manifest
file, no separate package format.

## What extensions can do

| API call                        | Effect |
|---------------------------------|--------|
| `api.on(event, handler)`        | Subscribe to a lifecycle event |
| `api.register_tool(name, ...)`  | Add (or override) an LLM-callable tool |
| `api.register_local_tool(agent, name, ...)` | Add a tool scoped to a specific handoff agent — see [agents.md](agents.md) |
| `api.register_command(name, ...)` | Add a `/name` slash command |
| `api.notify(message, level)`    | Print a line to stderr / chat log |
| `api.cwd`, `api.session_file`, `api.config_dir` | Read-only session context |

## Discovery order

Extensions are loaded from these locations, in this order. Project-local
extensions take precedence over global ones, so you can override a
shared extension on a per-project basis.

1. `<cwd>/.vtx/extensions/*.py` and `*/__init__.py`
2. `~/.vtx/agent/extensions/*.py` and `*/__init__.py`
3. `extensions:` list in `~/.vtx/config.yml`
4. `--extension PATH` (repeatable) on the CLI

To disable auto-discovery while still allowing explicit paths, pass
`--no-extensions`.

## Events

The full event surface:

| Event             | Fires when...                          | Blocking? |
|-------------------|----------------------------------------|-----------|
| `session_start`   | TUI / headless run begins              | no |
| `session_end`     | TUI / headless run ends                | no |
| `agent_start`     | User submitted a prompt                | no |
| `agent_end`       | Agent finished a turn                  | no |
| `turn_start`      | A new turn within the agent loop       | no |
| `turn_end`        | A turn completed                       | no |
| `tool_call`       | Right before a tool executes           | **yes**   |
| `tool_result`     | Right after a tool returns             | **yes**   |
| `compaction_start`| Conversation overflow triggered       | no |
| `compaction_end`  | Overflow summary written               | no |
| `agent_activated` | A handoff agent became active          | no |
| `agent_changed`   | The active handoff agent changed       | no |
| `tool_group_changed` | The active tool group for an agent changed (Alt+Ctrl+G) | no |

`session_start` and `session_end` are emitted synchronously because
they fire before / after the asyncio loop. Use a sync handler for
those events; for everything else, async handlers are preferred.

The `agent_activated`, `agent_changed`, and `tool_group_changed` events are part of the
[agents system](agents.md). `agent_activated` and `agent_changed` fire when the user picks a different
agent via `Shift+Tab`, `/agent <name>`, or the CLI `--agent` flag. `tool_group_changed` fires when
the user cycles the tool group via `Alt+Ctrl+G`. See
[agents.md](agents.md#events) for the payload shape.

## Blocking / modifying tool calls

The `tool_call` and `tool_result` events accept handler return values:

```python
from vtx.extensions import TOOL_CALL, TOOL_RESULT


def register(api):
    @api.on(TOOL_CALL)
    def gate(event, payload):
        # Block dangerous commands
        if payload["tool_name"] == "bash":
            cmd = (payload.get("args") or {}).get("command", "")
            if "rm -rf" in cmd:
                return {"block": True, "reason": "rm -rf is not allowed"}
        return None

    @api.on(TOOL_RESULT)
    def redact(event, payload):
        # Rewrite the text the LLM sees
        if payload["tool_name"] == "bash":
            text = " ".join(
                c.text for c in payload["result"].content if hasattr(c, "text")
            )
            if "SECRET" in text:
                return {"output": text.replace("SECRET", "[REDACTED]")}
        return None
```

Handler return values are processed in registration order, and later
handlers see earlier handlers' modifications. The first handler that
returns `{"block": True, ...}` short-circuits the call and the tool
never runs.

## Registering a custom tool

```python
def register(api):
    api.register_tool(
        name="greet",
        description="Greet someone by name",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Who to greet"}
            },
            "required": ["name"],
        },
        execute=lambda args, ctx: {
            "success": True,
            "result": f"Hello, {args['name']}!",
        },
    )
```

The `parameters` argument is a JSON Schema object — the same shape
LLM providers accept, so you can write `{"type": "array", "items": ...}`,
`{"type": "string", "enum": [...]}`, etc. vtx generates a pydantic
model from the schema, so the same parameters you write here are
what the LLM sees in its system prompt.

The `execute` callback may be sync or async. Return a `ToolResult`
(or any dict with `success`, `result`, `ui_summary`, `ui_details`,
`file_changes` keys).

### Overriding a built-in tool

If an extension registers a tool with the same name as a built-in
(`read`, `bash`, `web_search`, etc.), the extension version wins.
The original tool is not silently shadowed — the LLM sees your
description and your parameter schema instead. Use this for
auditing, sandboxing, or wrapping a built-in with a different
backend.

### Custom TUI rendering

Pass `ui_block=YourBlock` to `register_tool` / `register_local_tool`
to ship a custom :class:`vtx.ui.blocks.ToolBlock` subclass for the
tool. The chat log instantiates your class instead of the default
block; the bound `BaseTool` is exposed as `self.tool` so the block
can call back into the tool (e.g. `self.tool.format_call(params)`).

```python
from vtx.ui.blocks import ToolBlock

class CounterToolBlock(ToolBlock):
    """A block that shows a live counter alongside the default header."""

    def compose(self):
        # Inherit the default header/output/ask_user widgets…
        yield from super().compose()
        # …and add a custom widget underneath.
        yield Label("0", id="counter")

    def on_counter_tick(self, n: int) -> None:
        self.query_one("#counter", Label).update(str(n))

def register(api):
    api.register_tool(
        name="counter",
        description="Counts up to N",
        parameters={"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]},
        execute=lambda args, ctx: {"success": True, "result": str(args["n"])},
        mutating=False,
        ui_block=CounterToolBlock,
    )
```

See `examples/extensions/custom_tool_block.py` for a richer example
that renders a Rich `Table` inside the block. The same `ui_block`
kwarg is available on `api.local_tool(...)` for agent-scoped tools.

## Registering a custom slash command

```python
def register(api):
    api.register_command(
        name="hello",
        description="Say hello",
        handler=lambda args: f"hi {args or 'world'}",
    )
```

The handler receives the argument string (everything after `/hello`)
and may return a string or a `CommandOutcome(output=..., success=...)`.
Extension commands are looked up after the built-ins, so they can
shadow built-in commands if you really want to.

## Examples

See `examples/extensions/` in the source tree:

- `hello.py` — minimal lifecycle notifications
- `permission_gate.py` — block destructive `bash` commands
- `auto_commit.py` — git commit at the end of every successful turn
- `tool_override.py` — audit log for every `read` call
- `log_tool_calls.py` — JSONL log of every tool call + result

## Permissions and trust

Extensions run in-process with the same permissions as the vtx
binary. They can read and write any file you can, call out to the
network, and execute subprocesses. Don't load extensions from
sources you don't trust.

Vtx does not currently sandbox extensions. If you need stronger
boundaries, run vtx inside a container or VM and treat the
extension file as part of the image.

## Debugging

A bad extension should never crash the agent. If `register(api)`
raises, vtx logs the error to stderr and skips the extension. If
an event handler raises, the bus swallows the exception and
prints a traceback to stderr; the rest of the handlers still
fire and the agent loop continues.

To see which extensions vtx loaded, pass `--no-extensions` and
then load each one explicitly with `--extension PATH` until you
find the one that breaks. Extension errors are also logged at
launch time as `LaunchWarning` entries in the TUI.

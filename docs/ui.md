# Vtx TUI API

`vtx.ui` exposes the Textual interface as a clean Python surface. You can
import and embed individual widgets, drop in custom blocks, extend the
slash-command framework, or shell out to the full app entrypoint.

**For the extension hooks that let you modify vtx's TUI from an extension,
see [extensions.md](extensions.md#custom-tui-rendering).**

## Quick start

```python
from vtx.ui import Vtx, run_tui, ChatLog, InputBox, CommandsMixin
```

`Vtx` is the main Textual `App` subclass — wired at construction time with
model, provider, session resume, and agent overrides. `run_tui()` is the thin
launcher used by `cli.py` that instantiates it and prints the exit summary.

If you're embedding the TUI inside another app, compose `Vtx` yourself. If you
just want the interactive shell, call `run_tui(args)` with an
`argparse.Namespace` matching `cli.py`.

## Three ways to use `vtx.ui`

| Path | Use when |
|---|---|
| **Run the TUI** | You want the full vtx interactive shell. Call `run_tui(args)`. |
| **Embed / customize** | You want to compose your own Textual app with vtx widgets, blocks, and command mixins. Import from `vtx.ui` directly. |
| **Extend the running TUI** | You want to swap in custom tool blocks, inject widgets, or react to events from an extension. Use `ui_block` on `api.register_tool()` and the event bus. |

All three share the same runtime: `vtx.runtime.ConversationRuntime`,
`vtx.session.Session`, and `vtx.tools`.

---

## Core

| Symbol | Role |
|---|---|
| `Vtx` | Main Textual app. Mixes in commands, session UI, queue UI, completion UI, agent runner, and startup chores. |
| `run_tui(args)` | Launch the TUI. After `app.run()` returns, fires `session_end` on the extension bus and prints the exit hint block. |

`Vtx` accepts the same constructor arguments that `cli.py` parses:

```python
app = Vtx(
    model="gpt-4o-mini",
    provider="openai",
    api_key="sk-...",
    base_url=None,
    resume_session=None,
    continue_recent=False,
    openai_compat_auth_mode="none",
    anthropic_compat_auth_mode="none",
    extra_extension_paths=[],
    auto_discover_extensions=True,
    active_agent=None,
    extra_agent_paths=[],
    auto_discover_agents=True,
    initial_goal=None,
)
app.run()
```

### Mixin internals

`Vtx` is composed from mixins rather than a single monolithic class.
If you subclass it, override mixin methods in preference to the top-level
class:

| Mixin | File | Responsibility |
|---|---|---|
| `CommandsMixin` | `commands/__init__.py` | Slash-command router and domain handlers |
| `AgentRunnerMixin` | `agent_runner.py` | Driving agent runs and shell commands |
| `SessionUIMixin` | `session_ui.py` | Rendering persisted sessions into the chat log |
| `QueueUIMixin` | `queue_ui.py` | Pending / steer message queues |
| `CompletionUIMixin` | `completion_ui.py` | Completion list and selection-mode pickers |
| `StartupMixin` | `startup.py` | Background startup chores |
| `SessionUIMixin` | `session_ui.py` | Rendering persisted sessions into the chat log |

Mixin methods rely on the informal `Vtx` protocol in
`vtx.ui.app_protocol.Vtx`. If you're building a non-Textual host, the
protocol documents the attributes (`_cwd`, `_session`, `_provider`, etc.)
and methods (`query_one`, `run_worker`, `call_later`) the mixins expect.

---

## Widgets

| Symbol | Role |
|---|---|
| `ChatLog` | `VerticalScroll` container that renders the full message stream (user, assistant, tool, thinking). Supports scrolling, pruning, and streaming markdown. |
| `InputBox` | The prompt bar. Wraps Textual `TextArea` with autocomplete, path completion, prompt history, slash-command routing, and shell-style `!`/`!!` detection. |
| `InfoBar` | Top bar showing model, provider, session, permissions mode, thinking level, and git branch. Clickable to trigger settings/completion pickers. |
| `StatusLine` | Bottom strip showing token totals, context usage, background task count, and streamed thinking badge count. |
| `QueueDisplay` | Floating queue of pending and steer messages showing turn number, model, and tool list. |
| `FloatingList[T]` / `ListItem[T]` | Generic floating selection list used by completion/picker UIs. |
| `TreeSelector` | Tree navigation widget for session resume (`/tree`) — renders `vtx.session.TreeNode` as indented rows with connectors. |
| `format_path(path)` | Utility that shortens `$HOME` to `~` for display. |

### Composing your own app

The smallest embeddable vtx app is:

```python
from textual.app import App, ComposeResult
from textual.containers import Vertical

from vtx.ui import Vtx, ChatLog, InputBox, InfoBar, StatusLine, QueueDisplay


class MyApp(App):
    CSS = Vtx.CSS + Vtx.TITLE  # reuse vtx styles

    def compose(self) -> ComposeResult:
        yield InfoBar(id="info-bar")
        yield ChatLog(id="chat-log")
        yield QueueDisplay(id="queue-display")
        yield StatusLine(id="status-line")
        yield InputBox(id="input")
```

For a full, production-style app, subclass `Vtx` and override mixin
methods. The mixins handle event routing, so you can cherry-pick behavior:

```python
from vtx.ui import Vtx, ChatLog
from vtx.ui.agent_runner import AgentRunnerMixin


class MyApp(Vtx, AgentRunnerMixin):
    pass
```

If you're building something that only needs the chat log and input —
say, a minimal chat client — compose the widgets individually and drive
them with your own event loop.

---

## Message blocks

These `Static` widgets form the chat-log primitives. All accept
`rich.text.Text` or markdown-flavored content; most are internal
composition surfaces that `ChatLog` assembles automatically.

| Symbol | Role |
|---|---|
| `ContentBlock` | Base rendered message body. Handles streaming markdown, LaTeX preprocessing, and lazy wrapping. |
| `ThinkingBlock` | Renderable for `ThinkingContent` segments. Supports collapse/expand and highlights based on thinking depth. |
| `ToolBlock` / `TaskToolBlock` | Renderable for tool call + result pairs. Contains expandable output, schema args, and diff markers. |
| `UserBlock` | User message bubble. Handles inline image content and `ask_user` choice chips. |
| `HandoffLinkBlock` | Inline link rendered when an agent run ends with a handoff target. Click to resume that agent. |
| `LaunchWarning` | Dataclass (`message`, `severity`) plus `LaunchWarningsBlock` for non-fatal startup warnings (missing binaries, stale config, etc.). |
| `UpdateAvailableBlock` | Banner shown when the installed version is behind the PyPI release. |
| `stylize_badge_markers(text, markers)` | In-place badge styling for tool-result markers (`added`, `removed`, `modified`). |

### Building a custom block

Subclass one of the block primitives. The chat log treats any `Static`
as a block as long as the type is registered in `start_block()`:

```python
from textual.widgets import Static
from vtx.ui.blocks import ContentBlock

class MyBlock(ContentBlock):
    def compose(self):
        yield Static("Custom header")
        yield from super().compose()
```

The block lifecycle in `ChatLog`:

1. `ChatLog.start_block(type, widget)` — mounts the widget and adds a CSS class.
2. `ChatLog.append_to_current(text)` — streams text into the active block.
3. `ChatLog.end_block()` — closes the current block and snapshots for pruning.

Override `ChatLog.start_content()`, `start_thinking()`, `start_tool()`,
or `start_user()` to swap in your block classes.

---

## Slash-command framework

`CommandsMixin` composes the seven domain command classes and owns the
command router. Extend it or wrap it to add project-specific slash commands.

| Symbol | Role |
|---|---|
| `CommandsMixin` | Composes `SettingsCommands`, `ModelCommands`, `SessionCommands`, `AuthCommands`, `ProviderCommands`, `AgentCommands`, `GoalCommands`. Exposes `route_command()` and completion support. |

Individual command classes live in `vtx.ui.commands`:

| Class | Handles |
|---|---|
| `SettingsCommands` | `/settings`, `/themes`, `/permissions`, `/thinking`, `/notifications` |
| `ModelCommands` | `/model`, `/model refresh` |
| `SessionCommands` | `/clear`, `/new`, `/resume`, `/tree`, `/session`, `/handoff`, `/compact`, `/export`, `/copy` |
| `AuthCommands` | `/login`, `/logout` |
| `ProviderCommands` | `/provider` |
| `AgentCommands` | `/agent` |
| `GoalCommands` | `/goal` |

All importable from `vtx.ui.commands` (they have their own `__all__`).

### Adding a custom command

```python
from vtx.ui.commands.base import CommandSupport

class MyCommands(CommandSupport):
    def _handle_my_command(self, args: str) -> None:
        self.app.notify(f"Custom: {args}")

class MyApp(Vtx, MyCommands):
    pass
```

---

## Types and autocomplete

| Symbol | Role |
|---|---|
| `SelectionMode` | `StrEnum` for the active completion picker: `SESSION`, `MODEL`, `THEME`, `LOGIN`, `LOGOUT`, `PERMISSIONS`, `THINKING`, `THINKING_LINES`, `COLORED_TOOL_BADGE`, `NOTIFICATIONS`, `PROVIDER`, `SETTINGS`, `TREE`, `API_KEY`, `API_KEY_ACTION`. |
| `SlashCommand` | Dataclass (`name`, `description`, `category`) powering `/` inline completion. |
| `DEFAULT_COMMANDS` | Built-in `SlashCommand` list shipped with Vtx (generated from the command catalog). |
| `AutocompleteProvider` | ABC for custom inline completions (`FilePathProvider`, `PullRequestProvider`, etc.). |

### Adding an autocomplete provider

```python
from vtx.ui.autocomplete import AutocompleteProvider, SlashCommand

class MyProvider(AutocompleteProvider):
    @property
    def trigger_chars(self) -> set[str]:
        return {"/"}

    def complete(self, prefix: str, cursor: int) -> list[SlashCommand]:
        return [s for s in DEFAULT_COMMANDS if s.name.startswith(prefix.lstrip("/"))]
```

Register it in `InputBox`:

```python
from vtx.ui.input import InputBox

class MyInput(InputBox):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._autocomplete_providers.append(MyProvider())
```

---

## Utilities

| Symbol | Role |
|---|---|
| `format_tokens(n)` | Pretty-print an integer token count (short `k`/`M` form). |
| `get_styles()` | Returns the Textual CSS string. Driven by `config.ui.colors`; override `set_theme()` to change it. |
| `preprocess_latex(text)` | Convert `\frac`, `\sqrt`, `\sum`, `\int`, `\pm`, `sub/sup`, and `\hat` LaTeX commands into Unicode + markdown before Rich renders them. |
| `export_session_html(cwd, session_id, output_dir, version="")` | Standalone HTML export. Reads the JSONL session directly; returns the output `Path`. |

---

## Extending the running TUI with extensions

The extension system is the supported way to modify vtx's behavior without
forking. For UI customization, the two main hooks are:

1. **Custom tool blocks** — `ui_block=YourBlock` on `api.register_tool()`.
2. **Lifecycle notifications** — `api.notify()` and event subscriptions.

See [extensions.md](extensions.md#custom-tui-rendering) for the full
reference.

### Custom tool blocks

This is the primary supported UI extension point. When vtx instantiates a
tool call widget, it uses the `ui_block` class registered with the tool:

```python
from vtx.ui.blocks import ToolBlock
from textual.widgets import Label

class PingBlock(ToolBlock):
    """Custom block that shows 'PONG' in big text after execute."""

    def compose(self):
        yield from super().compose()
        yield Label("waiting...", id="reply")

    def set_result(self, ui_summary, ui_details, success, **kwargs):
        super().set_result(ui_summary, ui_details, success, **kwargs)
        if success:
            self.query_one("#reply", Label).update("PONG")


def register(api):
    api.register_tool(
        name="ping",
        description="Ping the server",
        parameters={"type": "object", "properties": {}},
        execute=lambda args, ctx: {"success": True, "result": "pong"},
        ui_block=PingBlock,
    )
```

The block constructor sets `block.tool = bound_tool`, so your block can
introspect parameters, call `self.tool.format_call(params)`, etc.

The built-in `TaskToolBlock` and `UpdateAvailableBlock` are good reference
implementations.

### Reacting to events from an extension

Subscriptions fire on the same `EventBus` the TUI listens to. You can't
directly push widgets into the TUI from an extension (there is no `api.push_widget`
surface), but you can:

- Call `api.notify()` — surfaced in the chat log as info messages.
- Modify `tool_result` output — the LLM sees your rewrite, and the TUI
  renders the modified result.
- Block or rewrite `tool_call` arguments.

```python
from vtx.extensions import AGENT_END


def register(api):
    @api.on(AGENT_END)
    def on_end(event, payload):
        api.notify(f"Agent finished: {payload.get('stop_reason')}")
```

If you need deeper widget injection, fork `vtx` and compose your own app
with `vtx.ui` widgets rather than relying on the extension protocol.

---

## Shared runtime dependencies

`vtx.ui` reuses the same runtime primitives as the SDK and headless paths:

- `vtx.runtime.ConversationRuntime` — agent loop.
- `vtx.session.Session` — JSONL persistence, tree compression.
- `vtx.tools` — tool registry (`get_tool`, `tools_by_name`, `DEFAULT_TOOLS`).
- `vtx.config` — `Config`, `get_config()`, all `set_*` helpers.
- `vtx.permissions` — `ApprovalResponse`, `AskUserOption`.

All of those live under `vtx` and are public via `vtx.__init__`'s `__all__`.
If you're building something that needs the runtime but not the TUI, import
from `vtx.sdk` or `vtx` directly to avoid the Textual/Pillow dependency chain.

---

## Dependencies

The UI surface pulls in Textual, Pillow, Rich, and pyyaml. If you only need
the runtime or SDK, import from `vtx.sdk` / `vtx` directly.

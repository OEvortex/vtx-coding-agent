# Extensions

An extension is a Python file that Vtx imports at boot and hands an `ExtensionAPI` to.
Use this page when you need to register an LLM provider, a message or entry renderer, or
a slash command from a file dropped on disk rather than declared as a plugin row.

For everything else - a tool, an event hook, a permission rule, a prompt section - prefer
a [plugin row](plugins.md). A row has real teardown, so disabling it while Vtx runs
leaves no copies behind. This page exists because the extension surface predates the row
model and is still the shortest path for providers and renderers.

## The entry convention

An extension file must define a `register` function. Vtx calls it once with the API:

```python
# ~/.vtx/agent/extensions/my_provider.py
from vtx.plugins.api import register


@register
def apply(api):
    ...
```

`ExtensionAPI` is implemented over the running composition's `Context`, so a
registration lands on the same tool registry, the same command registry, and the same
`EventHub` a plugin row would use. There is one of each, not two.

## The API surface

`src/vtx/plugins/api.py`.

### Registration

| Method | Purpose |
|---|---|
| `register_tool(name, description, parameters, *, execute, mutating=True, label=None, ui_block=None, tool_icon=None)` | Add an LLM-callable tool. Overriding a built-in's name replaces it. |
| `register_local_tool(...)` | Add a tool without exposing it to the model |
| `register_command(name, ...)` | Add a slash command |
| `register_provider(name, config)` | Register an LLM provider adapter |
| `unregister_provider(name)` | Remove a provider adapter |
| `register_message_renderer(custom_type, renderer)` | Render a custom session message type |
| `register_entry_renderer(custom_type, renderer)` | Render a custom session entry type |
| `register_flag(name, options)` / `get_flag(name)` | Read a user-set feature flag |
| `register_shortcut(shortcut, options)` | Bind a keyboard shortcut |

`register_tool` is at `src/vtx/plugins/api.py:234`; it builds an `ExtensionTool`, converts
the JSON Schema to a pydantic params model, and calls `mount_tool`, so teardown is wired.

Set `mutating=True` on a tool that changes state and it routes through the permission
gate; `mutating=False` tools never prompt.

### Events

`api.on(event, handler)` subscribes to the composition's `EventHub`. There are 44 event
names in `ALL_EVENTS` (`src/vtx/plugins/event_contract.py:77`).

```python
@api.on("tool_result")
async def on_tool_result(event, payload):
    ...
```

### Reaching the user and the session

| Method | Purpose |
|---|---|
| `notify(message, level="info")` | Surface a message in the TUI |
| `ui()` | The `ExtensionUIContext` for dialogs and footer state |
| `send_message(message, options=None)` | Append a custom message to the session |
| `send_user_message(...)` | Deliver a message as if the user sent it |
| `append_entry(custom_type, data=None)` | Append a custom session entry |
| `set_session_name` / `get_session_name` / `set_label` | Session metadata |
| `exec(command, args, options=None)` | Run a subprocess |
| `get_active_tools` / `get_all_tools` / `set_active_tools` | Inspect and change the tool set |
| `set_model` / `get_thinking_level` / `set_thinking_level` | Act on the running session |

`ExtensionAPI.on_session_start`, `on_turn_start`, `on_tool_call`, and the rest of the
`on_*` methods are registered but have **zero internal callers**. They are public surface
that nothing in this repo exercises, so a bug there is caught by nothing. Use
`api.on(event, handler)` instead, which is the path the built-ins use
(`src/vtx/plugins/builtins/hooks.py:114`).

`unregister_provider`, `register_flag`, and `register_shortcut` are likewise unreferenced
internally. Treat any of them as unverified until you have run it.

## Discovery paths

Vtx discovers extensions automatically and silently, with no prompt:

| Path | Scope |
|---|---|
| `.vtx/extensions/*.py` | This project |
| `~/.vtx/agent/extensions/*.py` | User-wide |

A file becomes one `extension:<stem>` row appended to the profile's row list, which is
why `/reload` and `vtx --dump-config` both affect extensions. A package extension with a
`pyproject.toml` can also declare entry points.

The same files can be named explicitly:

```sh
vtx --extension /path/to/ext.py      # repeatable
vtx --no-extensions                  # skip auto-discovery entirely
vtx --list-extensions                # print what mounted, with tools and commands
```

Auto-discovery order is project first, then user, then explicitly configured paths,
deduplicated by resolved path.

## Hot reload

`/reload` re-runs extension discovery and re-executes the files, so an edit takes effect
without a restart. The sequence is (`src/vtx/tui/commands/reload.py:70`):

1. outgoing extensions are told to let go, **before** the module cache is cleared
2. the imported extension modules are dropped, so a re-mounted file re-executes
3. config is reloaded first, because extension and agent discovery both read it
4. `reload_extensions` mounts the new set

The module-cache step is the subtle one: a *package* extension's submodules stay cached
in `sys.modules`, so editing a submodule of the same package needs a full restart rather
than `/reload`.

`/reload` is refused while a turn is running, because swapping the tool set mid-flight
leaves the advertised tools and the running tools out of sync. Wait for the turn or
interrupt it first.

## Providers

`register_provider(name, config)` adds a provider adapter. The config accepts a base URL,
an API key (an environment variable name or a literal), the API family, custom headers,
an auth header, a model list, and an OAuth block
(`ProviderConfig`, `src/vtx/plugins/api.py:429`).

See [providers.md](providers.md) for the 16 providers that already ship and how each
authenticates.

## Renderers

`register_message_renderer` and `register_entry_renderer` map a session message or entry
`custom_type` to a callable that renders it in the transcript. The type string is the
key, so it must match exactly what the producer appends via `append_entry`.

## Security

Extension code runs **in-process with the user's full permissions**. There is no sandbox,
no permission model, and no signature verification. `api.exec()` runs arbitrary commands
with the user's environment.

Two consequences worth stating plainly:

- **`.vtx/extensions/` is a repository supply-chain vector.** Anyone who can land a file
  in that directory in a project you open gets arbitrary code execution with your
  credentials on next start, with no prompt. This is materially the same trust model as a
  checked-in `.vscode/tasks.json`, but with no install step and no prompt at all.
- **The self-modification loop is unbounded.** The model can write a file, register a
  tool, and reload. There is no gate asking whether the human wanted that tool installed.

If you want that gate, keep the executable half out of extensions and use a plugin row,
where a change to `~/.vtx/profiles/<name>/cordis.yml` is a decision you can see and
review with `vtx --dump-config`.

## Known gaps

- No sandbox or isolation around extension code.
- The `ExtensionAPI.on_*` convenience methods have no internal callers, so they are
  unexercised public surface rather than a supported path.
- `register_flag`, `register_shortcut`, and `unregister_provider` are likewise
  unreferenced internally.
- A package extension's submodule edits need a restart, not `/reload`, because
  `sys.modules` is not cleared for subpackages.
- The old `register(api)`-style extension manager in `vtx.agent` was removed. The live
  path is `vtx.plugins.extensions`; `load_extension` and `load_all_extensions` in that
  module are superseded by `load_for_runtime` and are reachable only from tests.

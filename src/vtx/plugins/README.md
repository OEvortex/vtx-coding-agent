# vtx.plugins

The plugin kernel: one composition is a list of addressable plugin rows.

Read [../docs/plugins.md](../../docs/plugins.md) for the full contract. This page
covers the public surface of the package itself.

## Layout

| Module | Owns |
|---|---|
| `context.py` | `Context`: the service table, reversible effects, scoped children, typed service accessors |
| `events.py` | `DispatchMode` and `EventHub`: one dispatch mode per event name, waterfall middleware |
| `event_contract.py` | The event names `api.on` accepts, their dispatch modes, and how blocking returns merge |
| `plugin.py` | `Plugin`, the `plugin()` decorator, the `Service` base class, `as_plugin` |
| `loader.py` | `Row`, patch layers (`insert`/`set`/`replace`/`override`/`remove`), config validation, the import resolver |
| `host.py` | `PluginHost`: mount, unmount, dispose, status |
| `profile.py` | Profiles, bundles, `load_profile`, `dump_config` |
| `bootstrap.py` | `boot`, `active_host`, `active_context`, `reset` — the running composition |
| `install.py` | The `vtx plugin install` gate: compatibility check, exact-pair exemptions |
| `extensions.py` | The `extensions` row: discovery of `.vtx/extensions/*.py` and friends, one row per file |
| `api.py` | `ExtensionAPI` — the `register(api)` surface, implemented over `Context` |
| `tui.py` | `TUIChannel` + `install`/`uninstall` — the TUI as a `ctx.ui` provider |
| `services.py` | The typed façades: `ToolService`, `LLMService`, `SessionService`, … |
| `builtins/` | VTX's own subsystems as rows: `services/` provide `ctx.<key>`, `tools/` register one tool each |
| `errors.py` | `PluginError` and its subclasses |

## Minimal use

```python
from vtx.plugins import PluginHost, load_profile

host = PluginHost(load_profile("base"))
ctx = host.mount()
tools = ctx.tools.assemble()
host.dispose()
```

## Rules the kernel enforces

- A row that names a service nobody provides fails the whole mount, naming the key.
- A config that does not satisfy the plugin's pydantic schema fails with every
  offending field and the row id.
- Two rows in one layer with the same id fail; across layers the later wins.
- Re-declaring an event's dispatch mode with a different mode fails.
- `override` has no meaning in a bundle layer, which starts from an empty list.

Nothing here imports `vtx.coding_agent`. `context.py`, `events.py`, `plugin.py`,
`loader.py`, `host.py`, and `errors.py` import nothing from `vtx` at all;
`services.py`, `profile.py`, and `builtins/` reach upward deliberately.

Extensions are rows, not a parallel module tree: `extensions.py` discovers the
files and `api.py` is the `register(api)` object they receive. See
[docs/extensions.md](../../docs/extensions.md).

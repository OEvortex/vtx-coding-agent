# Plugin rows for Vtx

The real self-extension surface. A plugin row is one addressable line in a profile's row
list; every subsystem - the tool registry, the provider seam, the permission gate, the
session log - is itself a row, so a plugin replaces a subsystem rather than patching it.
Full reference: `references/docs/plugins.md` (bundled copy of the repo's `docs/plugins.md`).

## Two shapes

```python
# my_package/plugins.py
from vtx.plugins import plugin
from vtx.plugins.builtins.tools._register import mount_tool
from vtx.plugins.events import DispatchMode


@plugin(name="my-tool", inject=("tools",), config=MyConfig, meta={"owner": "me"})
def apply(ctx, config):
    """Runs once at mount. ``config`` is the row's config, validated."""
    mount_tool(ctx, MyTool())                      # unregistered when the row unloads
    ctx.permissions.add_rule(approve_reads_in_tmp) # rules can only tighten
    ctx.prompts.add_section("mine", lambda cwd, tools: "Always run the tests.")

    @ctx.on("session_start", mode=DispatchMode.SERIAL)
    async def on_start(session):
        ctx.ui.notify(f"started {session.id}")
```

```python
class p(Service):
    name = "p"
    inject = ("tools",)
    Config = MyConfig
    def start(self, ctx, config): ...
```

## The four rules that matter

1. **Never import another plugin.** Name its service with `inject`. A consumer listed
   above its provider still mounts, because activation is service-availability driven.
   A row whose service never appears fails the whole mount and names the missing key.
2. **Register through the context.** `ctx.effect`, `ctx.on`, and `mount_tool` all record
   teardown. Anything done outside them leaks past unload.
3. **`mount_tool`, not `ctx.tools.register`.** The helper wires the row's teardown, so
   disabling the row while Vtx runs does not leave two copies of the tool.
4. **Return a dict from a blocking handler.** At a mutating-tool seam,
   `{"block": True, "reason": ...}` denies; `{"args": {...}}` rewrites. Returning nothing
   delegates.

VTX has real teardown here, which prime-agent's extensions lack entirely - prime-agent's
`ExtensionFactory` has no `dispose`/`unload` callback at all, only a `session_shutdown`
event. Use the teardown you have: `ctx.effect` and `mount_tool`, and `host.dispose()`
unwinds newest-first.


## Service keys you can consume

`tools`, `llm`, `sessions`, `permissions`, `prompts`, `compaction`, `skills`,
`telemetry`, `sandbox`, `mcp`, `agents`, `agentLoop`, `sessionProjections`, `goals`,
`ui`, `commands`. Read through `ctx.<key>`; `ctx.agentLoop` and `ctx.agent_loop` are the
same service.

The three that are genuinely load-bearing, and are proven by tests that swap the service
and watch the real harness change (`tests/plugins/test_runtime_integration.py`):

| Seam | Who reads it | What a swap changes |
|---|---|---|
| `ctx.prompts` | `ConversationRuntime.resolve_system_prompt` | Every system prompt the model sees |
| `ctx.agentLoop` | `ConversationRuntime._new_agent` | Which driver builds the engine |
| `ctx.tools` | every tool row, `ToolService.assemble` | Which tools exist in this composition |

## Event dispatch

One mode per event name; a conflicting second registration fails at load, because the
mode is part of the contract.

| Mode | Awaited | Order | Returns |
|---|---|---|---|
| `emit` | yes | registration order | nothing |
| `waterfall` | yes | registration order | the final piped value |
| `parallel` | yes | all at once | every result |
| `serial` | yes | registration order | every result |
| `bail` | yes | registration order | the first non-`None` |

`waterfall` is around-middleware - the listener gets the piped value first and a `next`
callable last:

```python
async def decide(value, next, **kwargs):
    if is_forbidden(value):
        return "denied"          # short-circuit: downstream never runs
    return await next(value)     # observe, then delegate
```

Returning without calling `next()` is the design for a policy listener that owns the
decision. A listener that only annotates **must** delegate.

## Installing a row

Plugins are addressed in the profile's patch layer, `~/.vtx/profiles/<name>/cordis.yml`:

```yaml
- insert:                        # append, or whole-replace if the id exists
    - id: my-tool
      name: my_package.plugins:PLUGIN   # "module:attr"; bare "module" -> its PLUGIN export
      config:
        answer: 42

- override:                      # partial: replace only the fields named
    - id: tool-grep
      disabled: true

- remove:                        # drop the row entirely
    - tool-bash
```

Layers apply in order onto an empty list, **last write per row id wins**:
`profile's ordered bundles -> profile cordis.yml -> --patch overlays`. A row's `config`
is a whole value, not a merge.

`disabled` also takes a `!callable` predicate, called with the `Context` at every mount
decision:

```yaml
- id: telemetry-jsonl
  name: my_package.plugins:TELEMETRY
  disabled: !callable my_package.plugins:not_on_windows
```

## Commands

```sh
vtx --dump-config                     # the composed row list - any row printed here is replaceable
vtx --dump-config --profile headless
vtx plugin profiles                   # shipped profiles and bundles
vtx plugin init mine                  # create ~/.vtx/profiles/mine/
vtx plugin path mine
vtx plugin --profile mine disable tool-grep
vtx plugin --profile mine enable  tool-grep
vtx plugin --profile mine install git+https://github.com/me/my-plugin
vtx plugin --profile mine version-exemptions
vtx --profile mine --patch extra.yml  # boot with an overlay
vtx --profile minimal                 # boot the minimal composition
```

`enable`/`disable` rewrite only the `disabled` flag, appending an `override` when there is
none yet and leaving the rest of the file alone, so repeated toggles do not accumulate.

Shipped profiles: `default` (`base` + `tui`), `headless` (`base`), `minimal`.

## Version compatibility gate

A plugin declares which Vtx versions it was built against:

```toml
# my-plugin/pyproject.toml
[project.optional-dependencies]
vtx = [">=1.2,<2.0"]          # or: vtx = ["vtx-coding-agent>=1.2,<2.0"]
```

`vtx plugin install` checks this **before anything is downloaded and before any build
script runs**. An incompatible plugin refuses and prints the exemption command; the grant
lives in `~/.vtx/profiles/<name>/compatibility.json` as an exact `package@version` to
exact Vtx version pair. `allow-version` needs `--accept-risk`. Neither a plugin upgrade
nor a Vtx upgrade inherits a grant.

Note Vtx's own update check only understands strict numeric `MAJOR.MINOR.PATCH`
(`src/vtx/core/update_check.py:4`), so never declare or cut a prerelease like
`1.3.0rc1` - update detection will silently stop working.

## Embedding, and swapping a seam

```python
from vtx.plugins import PluginHost, load_profile, plugin
from vtx.plugins.builtins.tools._register import mount_tool

rows = load_profile("base")
host = PluginHost(rows, label="embedded")
host.add(plugin(name="my-tool", inject=("tools",))(lambda ctx, cfg: mount_tool(ctx, MyTool())))
ctx = host.mount()

tools = ctx.tools.assemble()
engine = ctx.agentLoop.create(provider, tools, session)

host.dispose()                              # unwinds everything, newest first
```

`host.add(plugin_obj)` mounts an object rather than an import path. Taking over an
existing row id substitutes your provider for that row, including re-enabling it - that
is how you swap `ctx.tools` or `ctx.agentLoop`.

`boot()`, `active_host()`, `active_context()` hold the composition the current process is
running; `reset()` disposes it. `VTX_PROFILE` selects a profile without a flag.

## Kernel symbols

| Symbol | Role |
|---|---|
| `plugin(name, inject, config, **meta)` | Decorator: function to `Plugin` |
| `Service` | Base class for an object plugin with `start(ctx, config)` |
| `as_plugin(obj)` | Normalise a function, `Service`, or duck-typed object |
| `Context` | Service table, effects, scoped children, typed accessors |
| `EventHub` | Handler registry, one dispatch mode per name |
| `PluginHost` | `add`, `mount`, `unmount`, `dispose`, `status` |
| `Row` | One addressable row (`id`, `name`, `disabled`, `config`, `meta`) |
| `apply_patch(rows, layer)` | Apply one patch layer, last write per id |
| `rows_from_layer(data)` | Normalise a bundle layer into `Row` objects |
| `validate_config(spec, row)` | Validate a row's config against the plugin's schema |
| `load_profile(name, patches)` | Compose a profile's full row list |
| `dump_config(name, patches)` | That list as plain dicts |
| `boot`, `active_host`, `active_context`, `reset` | Process-wide composition |

Errors are `PluginError` subclasses, each naming the offending row, service key, or
event: `RowError`, `ConfigError`, `DuplicateRowError`, `ServiceMissingError`, `CycleError`,
`EventModeError`, `PatchError`.

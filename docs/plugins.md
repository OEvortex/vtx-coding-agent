# Plugins

Vtx composes itself from a list of **plugin rows**. Every subsystem — the tool
registry, the provider seam, the session log, the agent loop, the permission
gate — is a row, so each is replaceable from configuration rather than by
editing the harness.

A row is one line of YAML (or one `Plugin` object). A **profile** is a named
ordered list of rows plus a patch layer the user owns.

> Design note: this mirrors DeepSeek Harness (`dsh`) and the Cordis kernel it
> vendors. The kernel is `vtx.plugins`; VTX's own subsystems are re-expressed as
> plugins in `vtx.plugins.builtins` rather than rewritten behind a new interface.

---

## The five ideas

| Idea | In VTX |
|---|---|
| A plugin is an object with `name`, `inject`, and `apply` | `@plugin(name=..., inject=("tools",))` over a function, or a `Service` subclass |
| A context is a repository of services | `ctx.tools`, `ctx.llm`, `ctx.sessions`, `ctx.agentLoop` — claimed by key, never imported |
| Dependencies are declared, not sequenced | `inject` names the keys a row needs; it mounts when they exist |
| Typed events with one dispatch mode each | `emit`, `waterfall`, `parallel`, `serial`, `bail` |
| Registrations are reversible effects | `ctx.effect(...)` / `ctx.on(...)`; unmounting unwinds them |

Activation is **service-availability driven**: row order carries no load
semantics. A row mounts as soon as every service it names exists, so a consumer
listed above its provider still works. A row whose service never appears fails
the whole mount and names the missing key — it is never skipped quietly.

---

## Writing a plugin

```python
# my_package/plugins.py
from vtx.plugins import plugin
from vtx.plugins.builtins.tools._register import mount_tool
from vtx.plugins.events import DispatchMode


def approve_reads_in_tmp(name, args):  # your own rule
    return "allow" if str(args.get("path", "")).startswith("/tmp") else None


@plugin(name="my-tool", inject=("tools",))
def apply(ctx, config):
    """Runs once at mount. ``config`` is the row's config, validated."""
    mount_tool(ctx, MyTool())            # unregistered when the row unloads
    ctx.permissions.add_rule(approve_reads_in_tmp)
    ctx.prompts.add_section("mine", lambda cwd, tools: "Always run the tests.")

    @ctx.on("session_start", mode=DispatchMode.SERIAL)
    async def on_start(session):
        ctx.ui.notify(f"started {session.id}")
```

Four things to internalise:

- **Never import another plugin.** Name its service with `inject`.
- **Register through the context.** `ctx.effect`, `ctx.on`, and the service's own
  register methods all record teardown. Anything you do outside them leaks.
- **`mount_tool`, not `ctx.tools.register`.** The helper wires the row's
  teardown so disabling the row while vtx runs does not leave two copies.
- **Return a dict from a blocking handler.** For `tools/execute`-style seams,
  `{"block": True, "reason": ...}` denies; `{"args": {...}}` rewrites.

### Plugin shapes

```python
@plugin(name="p", inject=("tools",), config=MyConfig, meta={"owner": "me"})
def apply(ctx, config): ...

class p(Service):
    name = "p"
    inject = ("tools",)
    Config = MyConfig
    def start(self, ctx, config): ...
```

`config` is a pydantic model. A plugin with no schema accepts any mapping; one
with a schema reports every offending field, with the row id, at load.

---

## Service keys

| Key | Provided by | Owns |
|---|---|---|
| `tools` | `builtins/services/tools.py` | The tool set **this composition** declares |
| `llm` | `…/services/llm.py` | The provider-adapter seam (`create`, `api_type`, `model`) |
| `sessions` | `…/services/sessions.py` | The durable session log |
| `permissions` | `…/services/permissions.py` | The mutating-tool gate; rules can only tighten |
| `prompts` | `…/services/prompts.py` | Ordered prompt sections + `build()` — what the runtime renders |
| `compaction` | `…/services/compaction.py` | Overflow detection, summarisation |
| `skills` | `…/services/skills.py` | The skill catalog a prompt indexes |
| `telemetry` | `…/services/telemetry.py` | The span exporters |
| `sandbox` | `…/services/sandbox.py` | The confined-script runner seam |
| `mcp` | `…/services/mcp.py` | External servers and the tools they expose |
| `agents` | `…/services/agents.py` | Agent profiles and the active selection |
| `agentLoop` | `…/services/agent_loop.py` | The driver `ConversationRuntime._new_agent` calls |
| `sessionProjections` | `…/services/projections.py` | Incremental folds over the log; `uses` ships built in |
| `goals` | `…/services/goals.py` | The same-session objective |
| `ui` | `builtins/ui.py` | The interactive channel (`notify`, `ask`) |
| `commands` | `builtins/commands.py` | Slash commands that cost no model turn |

A consumer reads a service through `ctx.<key>`; typing follows the key, so
`ctx.agentLoop` and `ctx.agent_loop` are the same service.

Three of these are genuinely load-bearing rather than decorative, and there are
tests that prove it by swapping the service and watching the real harness change:

| Seam | Who reads it | What a swap changes |
|---|---|---|
| `ctx.prompts` | `ConversationRuntime.resolve_system_prompt`, `_rebuild_system_prompt` | Every system prompt the model sees |
| `ctx.agentLoop` | `ConversationRuntime._new_agent` | Which driver builds the engine |
| `ctx.tools` | every tool row, and `ToolService.assemble` | Which tools exist in this composition |

See `tests/plugins/test_runtime_integration.py`.

---

## Events

Declare the mode once per event name; registering a conflicting mode fails at
load, because the mode is part of the event's contract.

| Mode | Awaited | Order | Returns |
|---|---|---|---|
| `emit` | yes | registration order | nothing |
| `waterfall` | yes | registration order | the final piped value |
| `parallel` | yes | all at once | every result |
| `serial` | yes | registration order | every result |
| `bail` | yes | registration order | the first non-`None` |

`waterfall` is around-middleware. A listener receives the piped value first and a
`next` callable last:

```python
async def decide(value, next, **kwargs):
    if is_forbidden(value):
        return "denied"          # short-circuit: downstream never runs
    return await next(value)     # observe, then delegate
```

Returning without calling `next()` is the design for a policy listener that owns
the decision. A listener that only annotates **must** delegate.

`emit` awaits its handlers but discards their results. A handler that must not
block the caller uses `hub.emit_nowait(...)`.

---

## Composition

### Profiles and bundles

A profile lives in `~/.vtx/profiles/<name>/`:

```
~/.vtx/profiles/default/
  bundle.txt     # ordered bundle names, one per line
  cordis.yml     # the user's patch layer
```

A **bundle** is a named set of rows shipped inside a package. The shipped ones:

| Bundle | Rows | Role |
|---|---|---|
| `base` | 25 | Services + the built-in tools. Every profile starts here. |
| `tui` | 2 | The interactive surface: `ctx.ui` and `ctx.commands`. |
| `headless` | 0 | Adds nothing — a serverless run boots `base` alone. |
| `minimal` | 4 | A tool registry, a provider seam, `read`, `bash`. For embedding. |

Shipped profiles: `default` (`base` + `tui`), `headless` (`base`), `minimal`.

Layers apply in order onto an empty list, **last write per row id wins**:

```
profile's ordered bundles  ->  profile cordis.yml  ->  --patch overlays
```
A row's `config` is a whole value, not a merge. That is deliberate: a row whose
value differs by mode lives in that mode's bundle, so every row is owned by
exactly one bundle plus the user's patch, and a later layer never has to know
what it is overwriting.

### The patch language

```yaml
# ~/.vtx/profiles/default/cordis.yml

- override:                      # partial: replace only the fields named
    - id: tool-grep
      disabled: true
    - id: llm
      config:
        default_provider: anthropic

- insert:                        # append, or whole-replace if the id exists
    - id: my-tool
      name: my_package.plugins:PLUGIN
      config:
        answer: 42

- remove:                        # drop the row entirely
    - tool-bash
```

`name` is `module:attr`; bare `module` resolves to that module's `PLUGIN` export.
`disabled` accepts a literal or a `!callable` predicate:

```yaml
- id: telemetry-jsonl
  name: my_package.plugins:TELEMETRY
  disabled: !callable my_package.plugins:not_on_windows
```

The predicate is called with the `Context` at every mount decision.

### Commands

```sh
vtx --dump-config                     # the composed row list
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

`enable`/`disable` rewrite only the `disabled` flag in `cordis.yml`, appending an
`override` when there is none yet and leaving the rest of the file alone. Every
row keeps exactly one home in the file, so repeated toggles do not accumulate.

`--dump-config` prints the tree your machine boots; **any row it prints can be
replaced by a patch of your own**.

### Installing a plugin, and refusing an incompatible one

A plugin declares which vtx versions it was built against:

```toml
# my-plugin/pyproject.toml
[project.optional-dependencies]
vtx = [">=1.2,<2.0"]          # or: vtx = ["vtx-coding-agent>=1.2,<2.0"]
```

`vtx plugin install` resolves the spec and checks it **before anything is
downloaded and before any build script runs**. An incompatible plugin refuses,
and the refusal prints the command that grants an exemption:

```
vtx plugin: demo-plugin is incompatible: requires vtx >=99.0, running 1.2.2
to install it anyway, grant an exemption for exactly this pair:
  vtx plugin --profile mine allow-version demo-plugin@1.0.0 --vtx-version 1.2.2 --accept-risk
```

The grant lives in `~/.vtx/profiles/<name>/compatibility.json` as an exact
`package@version` → exact vtx version pair. `allow-version` requires
`--accept-risk` and prints a warning first. Neither a plugin upgrade nor a vtx
upgrade inherits a grant, and a grant authorises nothing except that pair.

A registry or path spec is judged before install. A git URL or tarball can only
be judged after fetching, so an incompatible one is **uninstalled and rolled
back**, and a rollback that itself fails is reported rather than swallowed.

An editable checkout reports its version as `"editable"`, which cannot be
ordered against a requirement. That is treated as satisfied rather than crashing
the gate — `vtx.plugins.install.comparable("editable")` is `False`, so a caller
that needs to know can ask.

---

## Embedding

```python
from vtx.plugins import PluginHost, load_profile, plugin
from vtx.plugins.builtins.tools._register import mount_tool

rows = load_profile("base")                 # or "minimal"
host = PluginHost(rows, label="embedded")
host.add(plugin(name="my-tool", inject=("tools",))(lambda ctx, cfg: mount_tool(ctx, MyTool())))
ctx = host.mount()

tools = ctx.tools.assemble()                # the active tool list
engine = ctx.agentLoop.create(provider, tools, session)

host.dispose()                              # unwinds everything, newest first
```

`host.add(plugin_obj)` mounts a plugin object rather than an import path. Taking
over an existing row id substitutes your provider for that row — including
re-enabling it — which is how you swap `ctx.tools` or `ctx.agentLoop`.

`boot()`, `active_host()` and `active_context()` hold the composition the current
process is running; `reset()` disposes it. `VTX_PROFILE` selects the profile
without a flag.

---

## What is *not* a plugin yet

Honest accounting, so the claim is scoped:

- **The TUI's slash commands still dispatch through `CommandsMixin`.** The TUI
  *publishes* them as `ctx.commands` descriptors and installs its `notify`/`ask`
  implementation on `ctx.ui`, so a plugin can read the list and reach the person
  — but routing a command through `ctx.commands.dispatch` is not yet how the TUI
  runs one.
- **MCP connection is not a row.** The `ctx.mcp` seam exists and
  `McpService.connect` builds the manager, but the TUI still calls
  `_connect_mcp` directly rather than going through the service.
- **Session resume is not a row.** `ConversationRuntime.initialize` still calls
  `Session.continue_by_id` itself.
- No browser/Telemetry exporters are mounted beyond the optional JSONL one.

Running `vtx` boots the `default` profile before either surface starts, and the
runtime reads `ctx.prompts` and `ctx.agentLoop` from it — so the two seams a
third-party plugin is most likely to replace are already live. The four above
are the migration path, in the order you would take them.

---

## Kernel reference

| Symbol | Role |
|---|---|
| `plugin(name, inject, config, **meta)` | Decorator: function → `Plugin` |
| `Service` | Base class for an object plugin with `start(ctx, config)` |
| `as_plugin(obj)` | Normalise a function, `Service`, or duck-typed object |
| `Context` | Service table, effects, scoped children, typed accessors |
| `EventHub` | Handler registry with one dispatch mode per name |
| `PluginHost` | Mounts rows; `add`, `mount`, `unmount`, `dispose`, `status` |
| `Row` | One addressable plugin row (`id`, `name`, `disabled`, `config`, `meta`) |
| `apply_patch(rows, layer)` | Apply one patch layer, last write per id |
| `rows_from_layer(data)` | Normalise a bundle layer into `Row` objects |
| `validate_config(spec, row)` | Validate a row's config against the plugin's schema |
| `load_profile(name, patches)` | Compose a profile's full row list |
| `dump_config(name, patches)` | That list as plain dicts |
| `boot`, `active_host`, `active_context`, `reset` | Process-wide composition |

Errors are all `PluginError` subclasses and all name the offending row, service
key, or event: `RowError`, `ConfigError`, `DuplicateRowError`,
`ServiceMissingError`, `CycleError`, `EventModeError`, `PatchError`.

# How Vtx works

Vtx is a harness, not a monolith. Every subsystem - the tool registry, the provider
seam, the permission gate, the session log, the agent loop - is an addressable row in
a composition. The process boots a composition, resolves a system prompt from it, and
runs an agent against the tool set that composition declares.

This page is the map. [plugins.md](plugins.md) is the authoring contract.

## 1. The composition

A **row** is one addressable plugin with an `id`, a `name` (a `module:attr` import
path), an optional `config`, and a `disabled` flag. A **profile** is an ordered list of
bundles plus one YAML patch layer the user owns.

Vtx ships four bundles (`src/vtx/plugins/builtins/__init__.py:85`):

| Bundle | Role |
|---|---|
| `base` | 31 rows: services plus the built-in tools. Every profile starts here. |
| `tui` | The interactive surface: `ctx.ui` and `ctx.commands`. |
| `headless` | Adds nothing - a serverless run boots `base` alone. |
| `minimal` | A tool registry, a provider seam, `read`, `bash`. For embedding. |

And three profile templates:

| Profile | Bundles |
|---|---|
| `default` | `base` + `tui` |
| `headless` | `base` |
| `minimal` | `minimal` |

Layers apply onto an empty list in this order, **last write per row id wins**:

```text
profile's ordered bundles  ->  profile cordis.yml  ->  --patch overlays
```

A row's `config` is a whole value, not a merge. That is deliberate: a row whose value
differs by mode lives in that mode's bundle, so every row has exactly one home plus
the user's patch.

Print what your machine actually boots:

```bash
vtx --dump-config
```

## 2. Mounting is service-availability driven

A plugin declares what it needs with `inject`. Activation is not ordered by list
position - a row mounts as soon as every service it names exists, so a consumer listed
above its provider still works.

```python
@plugin(name="tool-mine", inject=("tools",))
def apply(ctx, config):
    mount_tool(ctx, MyTool())
```

A row whose service never appears fails the whole mount and names the missing key. It
is never skipped quietly (`ServiceMissingError`).

Registrations are reversible effects. `ctx.effect`, `ctx.on`, and `mount_tool` all
record teardown, and `host.dispose()` unwinds them newest-first. Anything a plugin
does outside those leaks past unload.

## 3. The agent loop

The CLI's first job is composition: resolve `--profile` and `--patch`, call
`vtx.plugins.boot`, then build a runtime (`src/vtx/coding_agent/cli.py`).

From there, one turn is:

1. `ConversationRuntime.resolve_system_prompt()` assembles the prompt from
   `ctx.prompts` - identity, environment, tool guidelines, project context, and the
   skill catalog (`src/vtx/agent/runtime.py:600`).
2. `ConversationRuntime._new_agent()` builds the engine from `ctx.agentLoop`
   (`src/vtx/agent/runtime.py:941`).
3. `ctx.tools.assemble()` produces the active tool list, which is what the provider
   serializes into tool schemas (`src/vtx/plugins/services.py:118`).
4. The model emits tool calls. Each mutating call goes through the permission gate
   first. Results stream back into the transcript.
5. The turn ends when the model stops calling tools.

Two of those seams are proven by tests that swap the service and watch the real
harness change (`tests/plugins/test_runtime_integration.py`): replacing `ctx.prompts`
changes every system prompt the model sees, and replacing `ctx.tools` changes which
tools exist.

## 4. How a tool reaches the model

This is the part most worth getting right, because it is where a registered tool
silently becomes invisible.

```text
mount_tool(ctx, tool)
  -> the row's tool map
  -> ctx.tools.assemble()          (src/vtx/plugins/services.py:118)
       -> the active list the provider serializes
            -> provider request: name, description, JSON schema
```

There is exactly one reliable channel: the provider's tool schemas. A tool that is
registered but not in the active set is invisible to the model, no matter how good its
description is.

A plugin can override a built-in by registering under the same name - the composition
replaces it (`ToolService.assemble`, `src/vtx/plugins/services.py:118`). That is how
you swap `bash`, and also how a buggy plugin can take `bash` away.

## 5. Events

Typed events, one dispatch mode per event name. Registering a conflicting mode fails
at load, because the mode is part of the contract.

| Mode | Awaited | Order | Returns |
|---|---|---|---|
| `emit` | yes | registration order | nothing |
| `waterfall` | yes | registration order | the final piped value |
| `parallel` | yes | all at once | every result |
| `serial` | yes | registration order | every result |
| `bail` | yes | registration order | the first non-`None` |

`waterfall` is around-middleware: the listener gets the piped value first and a `next`
callable last. Returning without calling `next()` is the design for a policy listener
that owns the decision; a listener that only annotates must delegate.

Prefer `ctx.on(...)` here. The parallel `ExtensionAPI.on_*` methods are registered but
have no internal callers, so a bug there is caught by nothing.

## 6. The system prompt

Assembled from ordered sections, not one blob (`src/vtx/agent/prompts/builder.py`):

1. `identity` - the base Vtx prompt
2. `env` - date, cwd, binary availability
3. `tooling` - aggregated per-tool guidelines
4. `skills` - the catalog, one `<skill>` entry per skill with name and description
5. project context - `AGENTS.md` and agent MDS files
6. git context, if enabled

Skills reach the model only through section 4, as a flat list of names and
descriptions. The body loads when the model opens the file, which is why a skill's
description is its routing signal and a vague description means the skill never fires.

## 7. Hot reload

`/reload` re-applies config, extensions, agents, tools, and context in place
(`src/vtx/tui/commands/reload.py`). It:

- tells outgoing extensions to let go, before clearing the module cache
- drops the imported extension modules so a re-mounted file re-executes
- reloads config first, because extension and agent discovery both read it
- rebuilds the tool row set and the event bus

It is refused while a turn is running, because swapping the tool set mid-flight
leaves the advertised tools and the running tools out of sync. Wait for the turn or
interrupt it.

There is no filesystem watcher. `/reload` or a restart is the only way new code lands.

## Known gaps

The codebase is honest about what is not yet a row (`docs/plugins.md` keeps the current
list):

- The TUI's slash commands still dispatch through `CommandsMixin` rather than
  `ctx.commands.dispatch`, so a plugin can read the command list but cannot yet route
  one through the service.
- MCP connection is not a row: the `ctx.mcp` seam exists and `McpService.connect`
  builds the manager, but the TUI still calls `_connect_mcp` directly.
- Session resume is not a row: `ConversationRuntime.initialize` still calls
  `Session.continue_by_id` itself.
- There is no persistent Python kernel. `codemode` runs a confined script in a
  subprocess per call, which is why there is no Python-backed skill runtime.

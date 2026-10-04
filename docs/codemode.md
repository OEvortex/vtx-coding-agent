# Code Mode

VTX ships a sandbox for model-written scripts. The model writes one small Python
program that calls only the tools you inject, and the program runs somewhere it
cannot reach anything you did not hand it.

```python
order = await tools.orders.lookup(id="order_42")
return {"id": order["id"], "needs_attention": order["status"] != "complete"}
```

One model turn, one tool call, several real operations — plus any filtering and
aggregation done in code instead of paid for in context.

## Using it

```python
from vtx.agent.codemode import CodemodeSandbox, CodemodeTool, Limits

async def lookup(args, _signal):
    return await db.fetch_one(args["id"])

sandbox = CodemodeSandbox(
    tools=[CodemodeTool(name="lookup", description="Look up an order by ID", execute=lookup)],
    limits=Limits(timeout_ms=30_000),
)

result = await sandbox.execute("return await tools.lookup(id='order_42')")
if result.ok:
    print(result.value)
else:
    print(result.diagnostic.kind, result.diagnostic.message)

await sandbox.close()
```

`CodemodeSandbox` is reusable. Each `execute()` is one-shot: a fresh process, a
fresh namespace, nothing carried over except what you put in the store.

## Using the built-in tools

`adapt_tools()` wraps VTX's own `BaseTool` implementations for the sandbox:

```python
from vtx.agent.codemode import CodemodeSandbox, adapt_tools
from vtx.agent.tools import get_all_tools

sandbox = CodemodeSandbox(tools=adapt_tools(list(get_all_tools().values())))
```

The adapter validates arguments through the same pydantic model the harness uses,
so a call the sandbox admits is one the harness would also accept.

It also bridges the one real mismatch between the two systems: `ToolResult.result`
is a **string**, while the sandbox boundary is JSON data. A result that parses as
JSON is handed to the script as structured data; one that does not is handed over
as a string. That distinction matters — a Python `repr` would arrive as a list
containing one string, and the script would then do string surgery on its own
output.

Two things the adapter does **not** do:

- **Permissions.** It calls `tool.execute()` directly, skipping
  `beforeToolCall`/`afterToolCall`. A host that gates tools must check there, the
  same as for any other direct invocation.
- **Filtering by default.** `adapt_tools()` exposes everything it is given. The
  filter is the authority boundary, so pass one deliberately:

  ```python
  adapt_tools(tools, predicate=lambda t: not t.mutating)
  ```

## What a script gets

`tools`, `store`, `load`, `text`, and `asyncio` are pre-bound, because those are
what a script is for: composing tool calls and shuttling values between them.

Beyond that, a script is ordinary Python. It has the **full standard library**,
installed third-party packages, the filesystem, subprocesses and the network -
see [How it is confined](#how-it-is-confined) for why that is deliberate. There is
no import allowlist; `import os`, `import pathlib`, `import requests` all work.

`asyncio` is bound for convenience rather than necessity: `gather`, `sleep`,
`wait_for` and `wait` are what a script reaches for, and `gather` is how
independent tool calls run concurrently. The whole module is importable too.

### Output

`text(value)` appends to the output the model reads, in order. `print` goes to a
discarded stream, so it is *not* shown to the model. Use `text`.

`image(value)` appends an image the model can see. It takes a base64 data URI, an
`{"image_url": ...}` object, or an image block taken straight out of an MCP
result. This exists because a tool that returns a picture had it flattened to
text before a script ever saw it, so there was no way to pass one on.

### Grouping

Tools are listed under their namespace, budgeted fairly across namespaces rather
than by a single global cheapest-first pass. With two hundred MCP tools on one
server and a dozen built-ins, a flat pass gives the cheap ones the budget and
leaves a second server's tools out entirely with no sign they exist. Here every
namespace is represented before any namespace is complete, and an empty one says
`none shown` rather than vanishing.

### The store

`store(key, value)` and `load(key)` carry JSON values between executions. The
sandbox persists nothing itself: you pass the current values in, and a
successful result reports what the script changed as `store_writes`.

```python
result = await sandbox.execute(code, store=saved)
if result.ok:
    for key in result.store_deletes:
        saved.pop(key, None)
    saved.update(result.store_writes)
```

A **failed or aborted run reports no writes**. A script that half-ran and then
raised leaves nothing behind.

Reads return a copy, so mutating a loaded value does not reach your state.

## Failures

Failures are data. `execute()` does not raise for a script that fails; it returns
`ok=False` and a `Diagnostic`.

The taxonomy exists so the model can recover accurately rather than treat
everything as "the script broke":

| kind | means |
| --- | --- |
| `script` | the script raised or failed to compile |
| `timeout` | the compute budget expired; the process was killed |
| `wall_clock` | the absolute ceiling expired while the script was waiting |
| `aborted` | the host signal fired, or the sandbox was closed |
| `sandbox` | the worker process or its transport failed |
| `stalled` | blocked on something nothing can complete |
| `unknown_tool` | the sandbox was not given that tool |
| `invalid_input` | the tool exists but rejected the arguments |
| `tool_failure` | the tool ran and declined |
| `invalid_output` | the tool's result is not JSON-safe |
| `host_unavailable` | no host bridge is wired up in this session |

The four tool kinds are raised *inside* the script as distinct exception classes,
which is something the JavaScript implementations cannot do:

```python
try:
    await tools.orders.lookup(id="nope")
except UnknownTool:
    ...          # wrong name — search instead
except ToolError as e:
    ...          # the tool declined — read e and change approach
```

To make a failure safe to show a model, raise `ToolError(message)` from a tool.
Only `message` crosses the boundary; an optional `detail` stays in your logs.
Anything unclassified is sanitized rather than forwarded, because a raw Python
traceback can leak host paths into the model's context.

## Finding a tool

Two routes, answering different questions.

**`tools.search`**, inside a script. BM25 over names, descriptions, and schema
property names *and their descriptions* — so a query naming a parameter finds the
tool that has it. An exact path (`orders.lookup` or `tools.orders_lookup`) is a
lookup rather than a keyword match, which beats a fuzzy one when the model
already knows the name. Every match carries the generated signature, so finding
a tool and knowing how to call it are one round trip.

It searches the whole callable set, not just what the catalog listed, and each
match says whether it was listed. A tool that is callable but unlisted is
exactly the one a model has least reason to know exists, so hiding it from the
search that exists to surface it would make it unreachable in practice rather
than merely unadvertised.

**`tool_search`**, as an ordinary tool call. It searches the same way, but a match
is *activated*: the tool is declared to the model on the next request. That is
the difference between "the model can find this if it thinks to look" and "the
model is shown this once it has said what it wants". Both are always callable —
`tools.search` is injected even when the catalog is complete, because advice that
names a tool which does not exist is worse than no advice.

## The `codemode` tool

The sandbox is registered in the tool registry, so the model reaches it the
usual way, and it is **default-on**: a tool the model has to be told about
before it uses it is a tool it will not think to use.

Its description carries the live tool catalog, so the model can write a script
in the same turn it learns what the script can call. It excludes itself from
that catalog — a script that could start a script would nest without limit —
and marks itself mutating, so the permission gate covers what a script does
rather than just that it ran.

Because a script can reach `bash`, a profile that wants to keep the shell out
of scripts has to say so. There is no longer a built-in `plan` profile to edit —
vtx ships no built-in agents at all, and a profile is a file you write. An
allow-list (`tools_allow`) is safe by construction; a deny-list must name
`codemode` explicitly, because denying `bash` while allowing `codemode` has not
restricted anything.

### Tools inside a script are governed

A tool a script calls is not exempt. It goes through the same extension hooks
(`tool_call`, `tool_execution_start`/`_end`), the same permission decision, and
the same argument rewriting as a call the model made directly.

The case that matters is the permission gate. It answers *allow* or *prompt*,
and **prompt is treated as a refusal** inside a script — a script has nobody to
ask. The model is told to call the gated tool directly so the user can approve
it. A script is a way to do the ungated calls together; it is not a way to get a
gated one done without the user seeing it.

A host that does not wire the gate up gets the old behavior: `adapt_tool` calls
`tool.execute` directly. `governed_invoker` is the wrapper that fixes it.

## MCP servers

A connected MCP server's tools are callable from a script, and a connected
server can publish more tools than fit in a prompt — which is the case code mode
exists for. The sandbox groups them under the server's namespace, and the
server's own `instructions` (its description of what its tools are for) becomes
that section's header, so it reaches the model without costing anything when no
script is written.

### A script gets the result, not the rendering

A tool result is flattened to text and truncated to 20KB for the model, which is
right there: a model should not pay 20KB of context for a table it only needed
three numbers from. A *script* is the opposite case, so an MCP tool's value
crosses into the sandbox as the whole `CallToolResult`:

```python
# Declared as -> CallToolResult[list[int]]
hits = await tools.mcp__docs__search(query="retries")
if hits["isError"]:
    text(hits["content"][0]["text"])   # the server said why
else:
    return hits["structuredContent"]
```

Three details make this work:

- **`structuredContent` is the typed data.** When a tool declares an output
  schema it is spelled into the declaration as `CallToolResult<...>`, so the
  model knows to index in rather than parse a string.
- **`isError` results resolve rather than raise.** A server that fails usually
  says why in its result, and a script that can read that and branch is doing
  something useful. Raising would discard the only explanation.
- **`_meta` is stripped.** It is server plumbing that routinely carries cursors
  and rate-limit state; a script has no use for it and the model's context is the
  last place it should land.

`image()` accepts an image block straight out of a result, so a tool that returns
a screenshot can be forwarded without re-encoding it. Remote `http` URLs are
refused — a script that could point the transcript at any host the model chose
would be an exfiltration primitive, not a feature.

## Source options

A script may begin with an options line:

```python
# @options: {"timeout_ms": 5000, "max_tool_calls": 20, "max_output_tokens": 4000}
```

The sandbox does not act on it; the host does. Every field is a *ceiling the
script may lower*, never raise — a script that asked for an hour does not get an
hour. The line is blanked rather than removed, so line numbers in a traceback
still match what you wrote.

Unknown fields are rejected rather than ignored — a model that sets a limit which
is silently dropped will believe it configured something it did not.

## How it is confined

**A script runs as ordinary Python. There is no interpreter-level confinement.**

This is worth stating first because everything else follows from it, and because
an earlier version of this document claimed otherwise. There is no
`sys.addaudithook`, no replaced `__builtins__`, and no import allowlist: a script
has full builtins, the full standard library, installed third-party packages,
the filesystem, subprocesses and the network, running as the user. Verified by
execution - a script can `import os, pathlib, subprocess`, read `os.environ`,
write files and call `eval`.

That is deliberate, and follows `ref/prime-agent`: a coding agent driving the
developer's own interpreter, where restricting the interpreter costs more model
capability than it buys. **A script is as trusted as the person running it.** It
is an isolation boundary for the *host*, not a security sandbox against the
*model*.

**What a script cannot reach is the host's live state** - the running agent, the
session, the TUI, and every tool - because all of that lives in the other
process. That is what the one real boundary buys:

**A separate process per execution.** A deadline is a `kill`, not a cooperative
request, so `while True:`, a pathological regex, and a runaway C extension all
die the same way rather than outliving the timeout. The budget counts only the
script's own running time — see [Two deadlines](#two-deadlines) — and an absolute
ceiling covers what it does not count.

If you need a capability withheld, `exposure` is the control that does it:
`codemode` makes a tool reachable from a script, `hidden` makes it unreachable
from scripts and from the model, and the permission gate still runs per call
inside the script (see [Tools inside a script are governed](#tools-inside-a-script-are-governed)).
The launcher is the file path rather than `-m`, so a run does not import the
agent SDK first; see [Launch cost](#launch-cost).

## Limits

Every knob is enforced. A limit that is accepted but does nothing is worse than
no limit at all, because the model will believe it set one.

| Limit | Default | Bounds |
| --- | --- | --- |
| `timeout_ms` | `30_000` | The script's *own* compute time, enforced by killing the process; `None` disables. Time blocked on a tool call is not charged — see [Two deadlines](#two-deadlines). |
| `wall_clock_ms` | `1_800_000` | Absolute ceiling on the whole execution, tool time included. Nothing pauses it. Host-only: a script cannot widen it. |
| `max_tool_calls` | `200` | Charged at *admission*, not completion — four calls already in flight have already been paid for, so checking at the end would let a script exceed the ceiling by exactly its own concurrency. |
| `max_output_tokens` | `8_000` | Applies to the text a script emitted. The middle is cut and the fact is reported; a silently shortened result reads as a complete one. |
| `memory_limit_bytes` | `512 MiB` | `RLIMIT_AS` inside the worker. A runaway allocation is otherwise only stopped by the deadline, so the user watches memory climb for all of it. Absent on Windows, where it degrades to the deadline. |
| `detect_stalls` | `True` | See below. Off when chasing a suspected false positive. |

A call that exceeds the budget is **refused, not truncated** — a catchable
`ToolError` inside the script, so it can stop and adapt instead of dying.

## Two deadlines

`timeout_ms` is not a wall clock, and the distinction is the whole reason a
script can delegate to a sub-agent.

The budget is charged only while the script is *running*. The moment a tool call
is outstanding the clock pauses, and it resumes with whatever was left when the
last reply lands. So `asyncio.gather` over four sub-agents waits as long as the
slowest one takes without spending a millisecond of the budget.

The reason is that the time is not the script's. A script blocked on a tool call
is waiting on the host, and under a single wall-clock deadline the host killed
the process while it was still waiting — then cancelled every in-flight call in
the same breath, so a fan-out died as a unit and a slow tool could never be
composed with a fast one.

That makes `timeout_ms` a bound on exactly what it was always for: a busy loop, a
pathological regex, a runaway C extension. Those spend their whole life running,
and a loop still dies on schedule.

The cost is that nothing bounds a tool call which never returns, so
`wall_clock_ms` does. It is absolute, nothing pauses it, and it reports
`wall_clock` rather than `timeout`, because the two failures want opposite
remedies: a `timeout` means the script's own work was too much and should be
split, while a `wall_clock` means it was waiting on something that never came
back and should stop waiting on it.

At the defaults that ceiling is 30 minutes, which no legitimate fan-out reaches.
It is a backstop, not a budget.

## Deadlock detection

The sandbox has exactly two ways to resume a blocked script: a tool reply
arriving, or a timer firing. A script that is blocked with **neither pending is
not slow, it is dead** — so the run ends in a millisecond with a `stalled`
diagnostic naming the cause, instead of sitting out the whole deadline and then
reporting a `timeout`. That distinction matters: a timeout sends the model off
splitting its work, when the actual bug is a promise that will never settle.

`asyncio.sleep` is available here, unlike in the JavaScript reference whose VM
has no timers, so a naive "blocked and no tool calls" check would call a
legitimate wait a deadlock. Timers are what separate the two, and they live on a
private loop attribute — so the check reports itself disabled when the internals
move, rather than firing wrongly.

## What a failure costs

A failed run reports no store writes, but it *does* report the calls it made, and
the TUI summary names the ones that failed. A script that half-ran usually
already had real side effects, and "Script failed (script)" alone leaves the user
no way to know whether the half that ran changed anything.

## Authority

The host owns authentication, tool selection, credentials, persistence, and
side effects. The sandbox owns parsing, the schema boundary, plain-data copying,
limits, and normalized diagnostics.

A program cannot gain authority through prose or generated code. It can only
exercise authority already present in the tools you injected. **Do not expose a
broad tool and expect the prompt to restrict it** — that rule is what the audit
hook enforces on everything else.
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
from vtx.ai.agent.codemode import CodemodeSandbox, CodemodeTool, Limits

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
from vtx.ai.agent.codemode import CodemodeSandbox, adapt_tools
from vtx.ai.agent.tools import get_all_tools

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

Inside the sandbox the script has `tools`, `store`, `load`, `text`, and `asyncio`,
plus a stripped `__builtins__`. It does **not** have the filesystem, the network,
subprocesses, `os`, `sys`, `eval`, `exec`, `compile`, `open`, `getattr`, or any
third-party package.

`import` works for a small standard-library allowlist (`json`, `re`, `math`,
`datetime`, `itertools`, `functools`, `collections`, `textwrap`, `string`,
`statistics`, `urllib.parse`, and a few more). Everything else raises with a
message naming the module.

`asyncio` is pre-bound rather than importable: `gather`, `sleep`, `wait_for`, and
`wait` are available, because `gather` is how independent tool calls run
concurrently. The rest of the module — the loop policy, the executor — is not.

### Output

`text(value)` appends to the output the model reads, in order. `print` goes to a
discarded stream, so it is *not* shown to the model. Use `text`.

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
| `timeout` | the deadline expired; the process was killed |
| `aborted` | the host signal fired, or the sandbox was closed |
| `sandbox` | the worker process or its transport failed |
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

## Discovery

With many tools, dumping every signature into the prompt is unaffordable.

`runtime.declarations()` renders signatures within a token budget and reports
whether the list is complete. `runtime.instructions()` adds the model-facing
workflow, rules, and language section, and says `COMPLETE` or
`PARTIAL - 12 of 340 shown` accordingly. That honesty matters: a model told the
list is exhaustive will never look for anything else.

`rank(query, tools)` is a BM25 ranker over tool names, descriptions, and input
schema property names *and their descriptions* — so a query naming a parameter
finds the tool that has it.

### The `search` tool

The sandbox always injects a `search` tool, even when the catalog fit entirely.
A model reading a `PARTIAL` list needs a way to discover what was left out, and
discovery advice that names a tool which does not exist is worse than no advice.

```python
matches = await tools.search(query="order status", limit=10, offset=0)
# -> {"matches": [{"path": "tools.orders_lookup", "name": "orders.lookup",
#                  "description": "...", "signature": "def tools...."}],
#     "next": {"offset": 10}, "remaining": 4}
```

Each match carries the generated signature, so finding a tool and knowing how to
call it are one round trip rather than two. An exact path (`orders.lookup` or
`tools.orders_lookup`) is a lookup that returns that one tool, which beats a
keyword match when the model already knows the name. An empty query browses
alphabetically, and `namespace` scopes to one top-level prefix.

The instructions advertise it only when the list is genuinely partial — a
`COMPLETE` list is a claim the model should be able to act on without a second
lookup.

## The `codemode` tool

The sandbox is registered in the tool registry, so the model reaches it the
usual way, and it is **default-on**: a tool the model has to be told about
before it uses it is a tool it will not think to use.

Its description carries the live tool catalog, so the model can write a script
in the same turn it learns what the script can call. It excludes itself from
that catalog — a script that could start a script would nest without limit —
and marks itself mutating, so the permission gate covers what a script does
rather than just that it ran.

Because a script can reach `bash`, it is denied by the read-only `plan` profile
alongside the tools it can reach. A deny-only profile needs `codemode` in
`tools_deny` explicitly; an allow-list profile is safe by construction.

## Source options

A script may begin with an options line:

```python
# @options: {"timeout_ms": 5000}
```

The sandbox does not act on it; the host does. A script may shorten the host's
deadline but never extend it. The line is blanked rather than removed, so line
numbers in a traceback still match what you wrote.

Unknown fields are rejected rather than ignored — a model that sets a limit which
is silently dropped will believe it configured something it did not.

## How it is confined

There is no wasm boundary in CPython, so confinement is three layers.

**A separate process per execution.** The script cannot reach host memory. A
deadline is a `kill`, not a cooperative request, so `while True:`, a pathological
regex, and a runaway C extension all die the same way.

**`sys.addaudithook`.** Installed before the script is compiled, denying
filesystem contents *and* discovery (`open`, `os.listdir`, `os.scandir`,
`os.walk`, `glob.glob`), every route to a second process, sockets, `ctypes`,
`marshal`, and the guard's own removal. Auditing is a CPython-level event, so it
fires however the operation is reached.

**A stripped namespace.** `__builtins__` is replaced with an allowlist, which
removes `eval`, `exec`, `compile`, `open`, `getattr`, `globals`, and
`__import__` in one move rather than by enumerating dangerous names.

### What is accepted

The CPython object graph is reachable. A script can walk
`().__class__.__base__.__subclasses__()` to a class whose `__init__.__globals__`
holds `os` or `subprocess`. This is a known introspection surface and cannot be
closed from Python.

It is not a hole, because the audit hook is the authority boundary and it fires
on the *operation*, not on how the object was reached. Verified against the
walked graph: `subprocess.run`/`Popen`/`os.popen` all raise `open`;
`_socket.socket().connect()` raises `socket.connect`; `os.open` + `os.read`
raises `open`; `os.fork` raises `os.fork`; `ctypes` and `_posixsubprocess` are
never resident at all.

What does leak is metadata that raises no audit event: `os.stat`, `os.access`,
`os.readlink`, `os.getcwd`, `os.uname`, `os.environ`. A script can confirm that
a path it already knows exists and read its size. No contents, no execution, no
network. Closing that needs a syscall filter (seccomp), which a portable
implementation cannot assume.

### Launch cost

The worker is a standalone module that imports nothing from `vtx`, launched by
file path rather than with `-m`. `python -m` would import `vtx/__init__` first,
which pulls in the agent SDK and costs ~1.5s on every execution; running the file
directly lands near 140ms.

That constraint is why the protocol constants and diagnostic kinds appear in both
this package and `sandbox.py`. `tests/test_codemode.py` asserts the two agree.

## Limits

One knob, and it is not optional in spirit: a script with no deadline is a
script that can wedge the session.

| Limit | Default | Bounds |
| --- | --- | --- |
| `timeout_ms` | `30_000` | wall clock, enforced by killing the process; `None` disables |

A call-count budget and an output-size cap would both be reasonable additions,
but neither is implemented, so neither is accepted — offering a limit that does
nothing is worse than not having one. Both are straightforward to add at the
host (a counter in `_serve`, a size check in `_result_from_frame`).

## Authority

The host owns authentication, tool selection, credentials, persistence, and
side effects. The sandbox owns parsing, the schema boundary, plain-data copying,
limits, and normalized diagnostics.

A program cannot gain authority through prose or generated code. It can only
exercise authority already present in the tools you injected. **Do not expose a
broad tool and expect the prompt to restrict it** — that rule is what the audit
hook enforces on everything else.
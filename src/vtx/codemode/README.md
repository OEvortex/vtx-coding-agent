# vtx.codemode

A confined process for model-written Python: the model writes one program that calls injected tools, sequences them, branches, and filters results in code instead of in context.

One important property, stated first because everything else follows from it: **there is no interpreter-level confinement.** The worker process (`sandbox.py`) installs no audit hook, replaces no `__builtins__`, and applies no import allowlist. A script is ordinary Python running as the user, with the full standard library, installed packages, filesystem, subprocesses and network. What a script cannot reach is the host's *live state* - the running agent, the session, the TUI, and every tool - because those live in the other process. Verified by execution: a script can `import os, pathlib, subprocess`, read `os.environ`, write files, and call `eval`.

The one thing the process boundary buys that confinement could not is that **a deadline is a `kill`**. `while True:`, a pathological regex, and a blocking syscall all end the same way.

## Responsibilities

- `host.py` - `CodemodeSandbox`: owns the process, the deadline, the abort signal, and every tool. Serves tool calls by round-trip over newline-delimited JSON on a pipe.
- `sandbox.py` - the worker. Imports **nothing** from `vtx`; the host launches it by file path (`python <path>/sandbox.py`, no `-m`) so `vtx/__init__` and the agent SDK are not imported on every execution.
- `declarations.py` - what the model reads: JSON Schema rendered as Python `def`-shaped signatures, plus the BM25 ranker that finds tools the catalog budget could not inline.
- `governance.py` - makes a script's nested tool calls obey the same hooks and permission gate as the model's own.
- `integration.py` - adapts the harness's `BaseTool` objects into `CodemodeTool`s.
- `source.py` - the `# @options:` line, the clamp functions, and a Lark grammar for providers that support grammar-constrained tool input.
- `errors.py` / `jsonio.py` - the diagnostic taxonomy and the one definition of "JSON-safe" shared by host and worker.

Not responsible for:

- **The `codemode` tool itself.** Registration, default-on policy, the store carried between calls, and the effective limits live in `vtx.agent.tools.codemode`.
- **Permissions.** `governance.py` applies whatever gate the host hands it; the gate itself is `vtx.core.permissions`.
- **Tool authoring.** `CodemodeTool.execute` is a plain `async (args, signal) -> Any`.
- **Catalog limits as configured in production.** `Limits()` defaults to `timeout_ms=30_000` and `None` everywhere else; the familiar `max_tool_calls=200`, `max_output_tokens=8_000`, `memory_limit_bytes=512 MiB` are the harness tool's constructor defaults, not this package's.

## Dependencies

- Imports (runtime): standard library, plus `vtx.codemode.*` only. `httpx` and `yaml` are **not** imported here.
- Imports (`TYPE_CHECKING` only, PEP 563): `from vtx.agent.tools import BaseTool` in `integration.py` and `governance.py`. This is deliberate - it keeps the sandbox free of a runtime `vtx.agent` edge. Note it is `vtx.agent.tools.BaseTool` (the concrete ABC), not the `BaseTool` Protocol in `vtx.protocol.abc`.
- Imports (lazy, inside functions, only when a host wires them up): `vtx.agent.extensions` (`TOOL_CALL`, `TOOL_EXECUTION_START`, `TOOL_EXECUTION_END`) and `vtx.core.permissions.PermissionDecision`, both in `governance.py`.
- Imported by: `vtx.agent.tools.codemode` (the tool) and `vtx.agent.tools.tool_search` (which reuses `declarations.rank`, `integration.base_tool_schema` and `types.CodemodeTool`). `vtx.mcp.tool` and `vtx.agent.context.skills` only mention it in prose.

## Public surface

`vtx.codemode.__all__` has 39 names.

### Host

| Name | Description |
|------|-------------|
| `CodemodeSandbox(*, tools=None, limits=None, catalog_budget_tokens=2000, python_executable=None, listed=None)` | Reusable: construct once, `execute()` many times. Each execution is a fresh process and namespace. Raises `ValueError` if a tool's identifier is `search` (reserved) or if `listed` names a tool that was not passed. |
| `CodemodeSandbox.execute(code, *, store=None, timeout_ms=None, max_tool_calls=None, max_output_tokens=None, signal=None) -> Result` | Runs one program. **Never raises for script failure** - `Result.ok`/`Result.diagnostic` is the outcome channel. The three budget overrides are the script's own requests, resolved min-wins against the sandbox limits. |
| `CodemodeSandbox.declarations() -> str` | The model-facing catalog within `catalog_budget_tokens`. |
| `CodemodeSandbox.instructions() -> str` | Workflow + rules + catalog. `tools.search` is advertised only when the catalog is genuinely partial or some tools are unlisted. |
| `CodemodeSandbox.tools -> tuple[CodemodeTool, ...]` | The injected tools, excluding the built-in search. |
| `CodemodeSandbox.close()` | Release the sandbox. |
| `SANDBOX_PATH` | `Path` to `sandbox.py`; what the host launches. |
| `truncate_middle(text, budget_tokens) -> (str, bool)` | Cut the middle out of over-long output, head and tail kept. Returns the flag so truncation is reported rather than silent. |

### Types

| Name | Description |
|------|-------------|
| `CodemodeTool` | Frozen dataclass: `name`, `description`, `execute`, `input_schema`, `output_schema`, `namespace`, `namespace_description`, `listed`. Schemas shape the declaration the model reads; values are **not** validated against them. `.identifier()` gives the Python name the script calls it by. |
| `Limits` | `timeout_ms=30_000`, `max_tool_calls=None`, `max_output_tokens=None`, `memory_limit_bytes=None`, `detect_stalls=True`. |
| `Result` | `ok`, `value`, `output`, `calls`, `diagnostic`, `store_writes`, `store_deletes`, `output_truncated`. `.apply_to_store(store)` commits staged writes. |
| `ToolCall` | One admitted call: `name`, `status`, `kind`, `message`, `duration_ms`, `.ok`. |
| `Diagnostic` | `kind`, `message`, `stack` - a failure as data, never a raw traceback. |
| `MAX_STORE_VALUE_CHARS` / `MAX_STORE_TOTAL_CHARS` | 256 KiB per store value, 1 MiB aggregate. |

### Errors

`CodemodeError` (base), `ToolError(message, *, detail=None, kind="tool_failure")`, and its subclasses `UnknownTool(name)`, `InvalidInput(name, *, detail=None)`, `InvalidOutput(what, *, detail=None)`, `HostUnavailable(detail=None)`; `ScriptError(message, *, stack=None)`, `SandboxError`, `ScriptAborted`, `ScriptStalled`, `ScriptTimeout`. `errors.KINDS` is the ten diagnostic kinds the host and worker mirror (`script`, `timeout`, `aborted`, `sandbox`, `stalled`, `unknown_tool`, `invalid_input`, `tool_failure`, `invalid_output`, `host_unavailable`).

### Governance

| Name | Description |
|------|-------------|
| `ToolGovernance(tool, *, run, extensions=None, permission=None, cancel_event=None)` | Runs one nested call behind the host's hooks and gate. |
| `governed_invoker(tools, *, run, extensions=None, permission=None, cancel_event=None) -> invoke` | Wraps every tool and returns one dispatcher for `adapt_tools(invoke=...)`. |

`ToolGovernance` emits `TOOL_EXECUTION_START` / `TOOL_CALL` / `TOOL_EXECUTION_END` with `tool_call_id=f"codemode/{name}"`. An extension may return `{"block": True, "reason": ...}` to refuse or `{"args": {...}}` to rewrite arguments; both are honoured, because an extension that rewrites arguments for the model must rewrite them for the script too. Unclassified exceptions are **not** forwarded to the script: the model gets `f"{name} failed"` and the detail stays in host logs.

The permission gate is three-way and the third case is the point. `check_permission` answers `ALLOW` or `PROMPT`, and `PROMPT` means *ask the user* - which a script has nobody to ask. So `PROMPT` is a refusal, and the model is told to call the tool directly so the user can approve it. A gate that raises is also a refusal, never a silent allow. Omitting `permission` entirely restores the pre-governance behaviour: `adapt_tool` calls `tool.execute` directly.

### Integration

| Name | Description |
|------|-------------|
| `adapt_tool(tool, *, namespace=None, namespace_description=None, listed=True, invoke=None) -> CodemodeTool` | Validates args through the tool's own pydantic `params` model, then runs it via `invoke(tool, args)` or `tool.execute(params)`. |
| `adapt_tools(tools, *, predicate=None, invoke=None, listed=None) -> list[CodemodeTool]` | No filtering by default. `predicate` is the reachability boundary; `listed` is the (separate) catalog-visibility boundary. |
| `base_tool_schema(tool) -> dict` | The tool's params schema, minus `title`, `$defs`, and per-property titles. |

Result mapping, in this order: a result carrying `.structured` returns it **as a value** (this is how an MCP `CallToolResult` reaches a script intact, so it can branch on `isError` and index `content`); otherwise a failed result raises `ToolError(ui_summary or result or "<name> failed")`; otherwise a string that parses as JSON is decoded, and one that does not is passed through as a string. Only a *successful* decode becomes data - handing the script a Python `repr` such as `[{'id': 1}]` would parse as a list of one string and the script would then do string surgery on its own output.

### Declarations and search

| Name | Description |
|------|-------------|
| `render_declarations(tools, *, budget_tokens=2000) -> (str, bool)` | Renders the catalog and reports honestly whether it is **complete**. Budget is allocated per namespace, so every namespace is represented before any is finished; an empty one prints `none shown`. |
| `render_signature(tool) -> str` | One `def tools.x(...)` line, with schema descriptions folded into the docstring. |
| `rank(query, tools, *, limit=10) -> list[SearchMatch]` | BM25 (`k1=1.5`, `b=0.75`) over name + description + schema property names **and their descriptions**, so a query naming a parameter finds the tool. camelCase is split before tokenizing. |
| `SearchMatch` | `(tool, score)`. |
| `to_identifier(name) -> str` | Total and deterministic name -> identifier mapping. camelCase is split, non-identifier characters collapse to `_`, a leading digit gains a `tool_` prefix, and a collision with the sandbox namespace (`tools`, `store`, `load`, `text`, `print`, `host_request`) gains a `_tool` suffix. |
| `is_mcp_result_schema(schema) -> bool`, `structured_content_schema(schema)` | Recognise an MCP result schema so the declaration can be spelled `CallToolResult<...>` instead of `dict`. |

Inside a script, `tools.search` is a host-implemented tool that is **always present** (a tool whose identifier is `search` is rejected at construction). It searches the whole callable set, not just what the catalog listed, and each match says whether it was listed. An exact path - `name`, `tools.name`, or the bare identifier - is a lookup and beats a keyword match. An empty query browses a namespace by name instead of matching nothing. `namespace`, `limit` (default 10) and `offset` are supported.

### Source options

`parse_source(source) -> SourceOptions` reads an optional leading line:

```python
# @options: {"timeout_ms": 5000, "max_tool_calls": 20, "max_output_tokens": 4000}
```

Known fields are exactly `timeout_ms`, `max_tool_calls`, `max_output_tokens`; anything else raises `CodemodeSourceError`, as do an empty script, malformed JSON, a non-object, an out-of-range value, and an options line with no code after it. The line is replaced by an **empty line**, not removed, so traceback line numbers still match what the model wrote.

`clamp_timeout(requested, host_limit)` and `clamp_int(requested, host_limit, *, floor=1)` resolve the effective budget. Min-wins: a script may ask for less and never for more. `CODEMODE_SOURCE_GRAMMAR` is a Lark grammar for providers that support grammar-constrained tool input.

## Usage

Real, and runnable as written:

```python
import asyncio
from vtx.codemode import CodemodeSandbox, CodemodeTool, Limits


async def double(args, signal):
    return {"n": args["n"] * 2}


async def main():
    tool = CodemodeTool(
        name="list-issues",
        description="List issues after a cursor",
        execute=double,
        input_schema={
            "type": "object",
            "properties": {"n": {"type": "integer", "description": "seed"}},
            "required": ["n"],
        },
    )
    sandbox = CodemodeSandbox(tools=[tool], limits=Limits(timeout_ms=30_000))

    result = await sandbox.execute(
        "results = await asyncio.gather(tools.list_issues(n=1), tools.list_issues(n=5))\n"
        "store('last', results)\n"
        "return {'results': results}"
    )
    if result.ok:
        print(result.value)        # {'results': [{'n': 2}, {'n': 10}]}
        print(result.calls)        # two ToolCall(name='list-issues', status='ok', ...)
        print(result.store_writes) # {'last': [{'n': 2}, {'n': 10}]}
    else:
        print(result.diagnostic.kind, result.diagnostic.message)

    await sandbox.close()


asyncio.run(main())
```

Inside a script the namespace is: `tools`, `store`, `load`, `text`, `image`, and the real `asyncio` module - plus full builtins, the full standard library, and third-party packages. The typed error classes (`ToolError`, `UnknownTool`, `InvalidInput`, `InvalidOutput`, `HostUnavailable`, `ScriptError`, `ScriptTimeout`, `ScriptAborted`, `ScriptStalled`, `SandboxError`) are injected so a script can branch on them:

```python
try:
    await tools.list_issues(n=1)
except UnknownTool:
    text("wrong name - try tools.search first")
except ToolError as e:
    text(f"the tool declined: {e}")
```

`text(value)` appends to the output the model reads, in order; non-strings are JSON-encoded. `print` goes to a discarded stream (`fd 1` is redirected to `/dev/null` before the script runs, alongside `fd 0`, so a `print` cannot corrupt the protocol frame - see `sandbox._reserve_fds`). `image(value)` takes a base64 data URI, an `{"image_url": ...}` object, or a raw MCP image block; remote `http`/`https` URLs are refused.

The store is not persisted by the sandbox: you pass the current values in, and a **successful** result reports what changed. A failed or aborted run reports no writes.

```python
saved: dict = {}
result = await sandbox.execute(code, store=saved)
if result.ok:
    result.apply_to_store(saved)
```

## Limits, in practice

| Limit | Enforced by | Notes |
| --- | --- | --- |
| `timeout_ms` | host | `SIGTERM` then `SIGKILL` after a 1.0s grace period, to the process group (`start_new_session=True`, so the host cannot be reached). `None` disables. |
| `max_tool_calls` | host | Charged at **admission**, not completion, so a script cannot exceed the ceiling by its own concurrency. Over budget is a catchable `ToolError` inside the script, not a truncated result. Verified: with `max_tool_calls=2`, a five-call script records 3 `ToolCall`s and fails with `tool_failure`. |
| `max_output_tokens` | host | Applies to the `text()` output; the middle is cut and `Result.output_truncated` is set. Floor 256. |
| `memory_limit_bytes` | worker | `RLIMIT_AS`, clamped to the existing hard limit, applied inside the worker before the script compiles. Absent on Windows, degrading to the deadline. |
| `detect_stalls` | worker | See below. |

A per-execution cap of `MAX_CONCURRENT_TOOL_CALLS = 8` in-flight tool calls is a fixed interpreter-side semaphore, not a host knob. Nested tool calls are refused past `_MAX_CALL_DEPTH = 16`.

## Deadlock detection: implemented, not reachable

`detect_stalls` defaults to `True` and the machinery is real (`sandbox._StallGuard`, `errors.ScriptStalled`, the `stalled` kind and its remedy text). The premise is sound: the worker has exactly two ways to resume a blocked script - a tool reply arriving, or a timer firing - so a script blocked with neither pending is dead.

**In the current wiring it never fires.** `_StallGuard.watch` builds `mine = {script_task, watcher_task}` and calls `_other_live_tasks(loop, mine)`, which returns every live task not in that set. `_run_script` is awaited from `runner()` in `sandbox.run`, and `runner` is always live and never in `mine`, so the check always sees one other task and always continues. Confirmed by direct experiment: a script awaiting a never-settling future reports `timeout` at the deadline, not `stalled`, and `asyncio.all_tasks()` during the block lists `runner`, the script task, the guard, and `main`. A one-line fix is to add the awaiting task to `mine`, but the behaviour is in the harness's favour for now: a timeout diagnostic is recoverable in the way the `stalled` remedy describes anyway.

## Authority

The host owns authentication, tool selection, credentials, persistence and side effects. The sandbox owns parsing, the JSON boundary, plain-data copying, limits and normalized diagnostics.

Because a script can touch the filesystem directly, which tools you *expose* no longer constrains what a script can *do* - only what it can do *through the harness*. Keep the permission gate in front of nested calls; treat the exposure decision as a statement about the user's machine, not as a security control. `vtx.codemode.__init__` says this too: "expose only the tools you want reachable" is no longer a confinement mechanism.
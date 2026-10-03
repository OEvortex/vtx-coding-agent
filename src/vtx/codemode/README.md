# vtx.codemode

Runs model-written Python in a worker process where the only capability the model is *given* is calling injected tools - and, importantly, that is a capability grant, not a containment mechanism. A script is ordinary Python: full builtins, full standard library, installed third-party packages, the filesystem, `subprocess`, the network, `os.environ`, `eval`, running as the user who launched vtx. There is no `sys.addaudithook`, no stripped `__builtins__`, no import allowlist. This follows the `ref/prime-agent` model and it is deliberate. What a script genuinely cannot reach is the host's *live state* - the running agent, the session, the TUI, the tool registry - because all of that lives in the other process. The consumers are the `codemode` tool in `vtx.agent.tools.codemode` and the tool searcher in `vtx.agent.tools.tool_search`, which reuses the declaration renderer and the ranker.

## Usage

Construct a sandbox, execute a script that composes several tool calls, inspect the result. This is runnable as written:

```python
import asyncio

from vtx.codemode import CodemodeSandbox, CodemodeTool, Limits


async def read_file(args, signal):
    with open(args["path"]) as fh:
        return fh.read()


async def word_count(args, signal):
    return len(args["text"].split())


async def main():
    sandbox = CodemodeSandbox(
        tools=[
            CodemodeTool(
                name="read-file",
                description="Read a file as text",
                execute=read_file,
                input_schema={
                    "type": "object",
                    "properties": {"path": {"type": "string", "description": "path to read"}},
                    "required": ["path"],
                },
            ),
            CodemodeTool(
                name="word-count",
                description="Count words in a string",
                execute=word_count,
                input_schema={
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
            ),
        ],
        limits=Limits(timeout_ms=15_000),
    )

    result = await sandbox.execute(
        """
import json, pathlib

counts = {}
for name in sorted(p.name for p in pathlib.Path(".").glob("*.toml")):
    src = await tools.read_file(path=name)
    counts[name] = await tools.word_count(text=src)

biggest = max(counts, key=counts.get)
text(json.dumps(counts))
return {"files": len(counts), "biggest": biggest, "words": counts[biggest]}
"""
    )

    if result.ok:
        print(result.value)
        # {'files': 2, 'biggest': 'pyproject.toml', 'words': 287}
        print(result.output)
        # ({'type': 'text', 'text': '{"pyproject.toml": 287, "ty.toml": 7}'},)
        print([(c.name, c.status) for c in result.calls])
        # [('read-file', 'ok'), ('word-count', 'ok'), ('read-file', 'ok'), ('word-count', 'ok')]
    else:
        print(result.diagnostic.kind, result.diagnostic.message)
        print(result.diagnostic.stack)

    await sandbox.close()


asyncio.run(main())
```

`execute()` never raises for a script failure; `Result.ok` and `Result.diagnostic` are the outcome channel. Arguments are keyword-only, so `tools.read_file("pyproject.toml")` is a `TypeError` inside the script, not a convenience. The name is always mapped to a Python identifier: `read-file` is `tools.read_file`, and `tools["read-file"]` does **not** work - the dict-like surface is only iteration, `len`, and `keys()`, all of which report identifiers. The namespace inside the script is `tools`, `store`, `load`, `text`, `image`, the typed error classes, and the real `asyncio` - on top of everything Python already has. `text(value)` appends to `Result.output`, JSON-encoding non-strings. `print` goes to a discarded stream: fds 0 and 1 are redirected to `/dev/null` before the script runs (`sandbox._reserve_fds`) so a `print` cannot corrupt the newline-delimited JSON protocol frame. `image(value)` takes a base64 `data:` URI, an `{"image_url": ...}` object, or a raw MCP image block; remote `http`/`https` URLs are refused.

## The host

- `CodemodeSandbox(*, tools=None, limits=None, catalog_budget_tokens=2000, python_executable=None, listed=None)` is reusable: construct once, `execute()` many times. Each execution is a fresh process and a fresh namespace. Construction raises `ValueError` if a tool's identifier is `search` (reserved for the built-in) or if `listed` names a tool that was not passed. `host.py` owns the process, the deadline, the abort signal, and every tool, and serves tool calls by round trip over a pipe.

- `CodemodeSandbox.execute(code, *, store=None, timeout_ms=None, max_tool_calls=None, max_output_tokens=None, signal=None)` runs one program. The three budget overrides are the *script's* requests and resolve min-wins against the sandbox limits, so a script can ask for less and never for more.

- `CodemodeSandbox.declarations()` returns the model-facing catalog within `catalog_budget_tokens`; `instructions()` returns workflow + rules + catalog, and advertises `tools.search` only when the catalog is genuinely partial or some tools are unlisted. `sandbox.tools` is the injected tuple, excluding the built-in search. `close()` releases the sandbox.

- `SANDBOX_PATH` is the `Path` to `sandbox.py`, which is what the host launches - by file path (`python <path>/sandbox.py`, no `-m`), so `vtx/__init__` and the agent SDK are not imported on every execution. `sandbox.py` imports nothing from `vtx` at all.

- `truncate_middle(text, budget_tokens) -> (str, bool)` cuts the middle out of over-long output, keeping head and tail, and returns the flag so truncation is reported rather than silent.

- `Limits` carries `timeout_ms=30_000`, `max_tool_calls=None`, `max_output_tokens=None`, `memory_limit_bytes=None`, `detect_stalls=True`. Those are this package's defaults; the familiar `max_tool_calls=200`, `max_output_tokens=8_000`, `memory_limit_bytes=512 MiB` are the harness tool's constructor defaults, not these. A per-execution cap of `MAX_CONCURRENT_TOOL_CALLS = 8` in-flight tool calls is a fixed interpreter-side semaphore, not a host knob, and nested tool calls are refused past depth 16.

## Results and diagnostics

`Result` carries `ok`, `value`, `output`, `calls`, `diagnostic`, `store_writes`, `store_deletes`, and `output_truncated`; `.apply_to_store(store)` commits the staged writes. `ToolCall` is one admitted call - `name`, `status`, `kind`, `message`, `duration_ms`, `.ok`. `Diagnostic` is `kind`, `message`, `stack`: a failure as data, never a raw traceback.

`errors.KINDS` is the ten kinds host and worker mirror: `script`, `timeout`, `aborted`, `sandbox`, `stalled`, `unknown_tool`, `invalid_input`, `tool_failure`, `invalid_output`, `host_unavailable`. The exceptions behind them are `CodemodeError` (base), `ToolError(message, *, detail=None, kind="tool_failure")` with subclasses `UnknownTool(name)`, `InvalidInput(name, *, detail=None)`, `InvalidOutput(what, *, detail=None)`, `HostUnavailable(detail=None)`, plus `ScriptError(message, *, stack=None)`, `SandboxError`, `ScriptAborted`, `ScriptStalled`, and `ScriptTimeout`. The same names are injected into the script so it can branch on them:

```python
try:
    await tools.read_file(path="pyproject.toml")
except UnknownTool:
    text("wrong name - try tools.search first")
except ToolError as e:
    text(f"the tool declined: {e}")
```

Observed edges:

```python
await sandbox.execute("raise ValueError('boom')")
# ok=False, kind='script', message='ValueError: boom'
await sandbox.execute("import asyncio\nawait asyncio.sleep(10)")   # 5s deadline
# ok=False, kind='timeout'
await sandbox.execute("await tools.nonexistent()")
# ok=False, kind='unknown_tool'
```

## Limits

`timeout_ms` is enforced by the host: `SIGTERM` to the process group (`start_new_session=True`, so the host is not reachable either) then `SIGKILL` after a 1.0s grace period. `None` disables it. `max_tool_calls` is spent at **admission**, not at completion, so a script cannot exceed the ceiling by its own concurrency; over budget is a catchable `ToolError` inside the script rather than a truncated result. `max_output_tokens` applies to the `text()` output and sets `Result.output_truncated` (floor 256). `memory_limit_bytes` is an `RLIMIT_AS` applied by the worker before the script compiles, clamped to the existing hard limit and absent on Windows, where it degrades to the deadline. `clamp_timeout` and `clamp_int` are the min-wins resolvers behind the `execute()` overrides.

The one thing the process boundary buys that interpreter-level confinement could not is that a deadline is a `kill`: `while True:`, a catastrophic regex, and a blocking syscall all end the same way.

## Store

`store(key, value)` and `load(key)` are synchronous JSON values that survive across executions. The sandbox persists nothing itself - you pass the current values in as `store=`, and only a **successful** result reports what changed, so a failed or aborted run reports no writes:

```python
saved: dict = {}
result = await sandbox.execute(code, store=saved)
if result.ok:
    result.apply_to_store(saved)
```

`load` returns a copy, so mutating it does not change the store. Storing `None`-valued keys is a delete. A value may be at most `MAX_STORE_VALUE_CHARS` (256 Ki) of JSON and all values together at most `MAX_STORE_TOTAL_CHARS` (1 Mi).

## Declarations and search

What the model reads is Python, not JSON Schema: `render_declarations(tools, *, budget_tokens=2000) -> (str, bool)` renders the catalog and reports honestly whether it is **complete**. The budget is allocated per namespace so every namespace is represented before any is finished, and an empty one prints `none shown`. `render_signature(tool)` is the one-line form:

```python
# def tools.read_file(path: str) -> Any:
#     """Read a file as text"""
#     # path: path to read
```

Schemas only shape the declaration; values are **not** validated against them.

Inside a script, `tools.search` is a host-implemented tool that is always present. It searches the whole callable set, not just what the catalog listed, and each match says whether it was listed:

```python
await sandbox.execute("return await tools.search(query='write', limit=2)")
# {'matches': [{'path': 'tools.write_file', 'name': 'write-file',
#               'description': ..., 'signature': ..., 'listed': True}], ...}
```

An exact path - `name`, `tools.name`, or the bare identifier - is a lookup and beats a keyword match; an empty query browses a namespace by name instead of matching nothing. `namespace`, `limit` (default 10) and `offset` are supported.

`rank(query, tools, *, limit=10)` is the BM25 ranker (`k1=1.5`, `b=0.75`) behind it, over name + description + schema property names *and their descriptions*, so a query naming a parameter finds the tool; camelCase is split before tokenizing, and matches come back as `SearchMatch(tool, score)`. `to_identifier(name)` is the total, deterministic name-to-identifier mapping: `my-tool` becomes `my_tool`, `myTool` becomes `my_Tool`, `9lives` gains a `tool_` prefix, and a collision with the sandbox namespace becomes `tools_tool`. `is_mcp_result_schema` and `structured_content_schema` recognise an MCP result schema so the declaration can be spelled as such instead of `dict`.

## Governance

Nested tool calls go through the same hooks and the same permission gate as the model's own calls - an orchestrator does not get a weaker version of the governance that applies to it.

- `ToolGovernance(tool, *, run, extensions=None, permission=None, cancel_event=None)` runs one nested call. It emits `TOOL_EXECUTION_START`, `TOOL_CALL`, and `TOOL_EXECUTION_END` with `tool_call_id=f"codemode/{name}"`. An extension may return `{"block": True, "reason": ...}` to refuse or `{"args": {...}}` to rewrite arguments; both are honoured, because an extension that rewrites arguments for the model must rewrite them for the script too. Unclassified exceptions are **not** forwarded to the script - the model gets `f"{name} failed"` and the detail stays in host logs.

- `governed_invoker(tools, *, run, extensions=None, permission=None, cancel_event=None)` wraps every tool and returns one dispatcher for `adapt_tools(invoke=...)`.

The permission gate is three-way and the third case is the point. `check_permission` answers `ALLOW` or `PROMPT`, and `PROMPT` means *ask the user* - which a script has nobody to ask. So `PROMPT` is a **refusal** inside a script, and the model is told to call that tool directly so the user can approve it. A gate that raises is also a refusal, never a silent allow. Omitting `permission` restores the pre-governance behaviour, in which `adapt_tool` calls `tool.execute` directly.

## Integration

`adapt_tool(tool, *, namespace=None, namespace_description=None, listed=True, invoke=None) -> CodemodeTool` validates arguments through the tool's own pydantic `params` model, then runs it. `adapt_tools(tools, *, predicate=None, invoke=None, listed=None)` does the same for a list and filters nothing by default: `predicate` is the reachability boundary, `listed` is the separate catalog-visibility boundary. `base_tool_schema(tool)` is the tool's params schema minus `title`, `$defs`, and per-property titles.

Result mapping, in order: a result carrying `.structured` returns it **as a value** - this is how an MCP `CallToolResult` reaches a script intact, so it can branch on `isError` and index `content`; otherwise a failed result raises `ToolError(ui_summary or result or "<name> failed")`; otherwise a string that parses as JSON is decoded, and one that does not is passed through as a string. Only a *successful* decode becomes data - handing the script a Python `repr` such as `[{'id': 1}]` would parse as a list of one string and the script would then do string surgery on its own output.

## Source options

`parse_source(source) -> SourceOptions` reads an optional leading line:

```python
# @options: {"timeout_ms": 5000, "max_tool_calls": 20, "max_output_tokens": 4000}
```

The known fields are exactly those three; anything else raises `CodemodeSourceError`, as do an empty script, malformed JSON, a non-object, an out-of-range value, and an options line with no code after it. The line is replaced by an **empty line**, not removed, so traceback line numbers still match what the model wrote. `# @options` clamps rather than widens: `timeout_ms=999999` under a 5s sandbox deadline is 5s. `CODEMODE_SOURCE_GRAMMAR` is a Lark grammar for providers that support grammar-constrained tool input.

## Deadlock detection: implemented, not reachable

`detect_stalls` defaults to `True` and the machinery is real (`sandbox._StallGuard`, `ScriptStalled`, the `stalled` kind and its remedy text). The premise is sound: the worker has exactly two ways to resume a blocked script - a tool reply arriving, or a timer firing - so a script blocked with neither pending is dead.

**In the current wiring it never fires.** `_StallGuard.watch` builds `mine = {script_task, watcher_task}` and asks for every live task *not* in that set. `_run_script` is awaited from `runner()` in `sandbox.run`, and `runner` is always live and never in `mine`, so the check always sees another task and always continues. Confirmed by experiment: a script awaiting a never-settling future reports `timeout` at the deadline, not `stalled`.

## What this package is not responsible for

The `codemode` tool itself (registration, default-on policy, the store carried between calls, and the effective production limits) lives in `vtx.agent.tools.codemode`. The permission gate itself is `vtx.core.permissions`; `governance.py` only applies whatever gate the host hands it. `CodemodeTool.execute` is a plain `async (args, signal) -> Any`, so tool authoring needs nothing from here. `imports`: standard library plus `vtx.codemode.*` only - `httpx` and `yaml` are not imported. The `vtx.agent.tools.BaseTool` references in `integration.py` and `governance.py` are `TYPE_CHECKING`-only, deliberately, to keep the sandbox free of a runtime `vtx.agent` edge.

Because a script can touch the filesystem directly, which tools you *expose* no longer constrains what a script can *do* - only what it can do *through the harness*. Keep the permission gate in front of nested calls, and treat the exposure decision as a statement about the user's machine rather than as a security control.
# Codemode

Codemode runs model-written Python in a worker process, where the only capability the script is given is calling injected tools. This page covers how a script is admitted and run, the governance gate its tool calls pass through, and how to drive the sandbox directly from your own code. To see how codemode is exposed to the model as a tool, read [tools.md](tools.md).

Read [mcp.md](mcp.md) for how MCP servers reach a script, [plugins.md](plugins.md) for the row that mounts the tool, and [storage-layout.md](storage-layout.md) for the disk footprint of one.

Runs as the user who launched vtx, with the full standard library, installed third-party packages, the filesystem, `subprocess`, the network, and `eval`. There is no `sys.addaudithook`, no stripped `__builtins__`, and no import allowlist. Treat it as a **capability grant, not a containment mechanism** (`src/vtx/codemode/README.md:1`). What a script genuinely cannot reach is the host's live state  --  the running agent, the session, the TUI, and the tool registry all live in the other process.

Since the python-skill mechanism was removed, codemode is the only execution path left: a `pyproject.toml` in a skill directory is ignored and skills are markdown instructions only (`docs/skills.md:5`, `src/vtx/coding_agent/tools/skill.py:226`). If you want executable behavior, write a plugin row  --  see [plugins.md](plugins.md).

## How a script is admitted

The worker is `src/vtx/codemode/sandbox.py`. The host spawns one subprocess per `execute()` call by launching that file **by path**, not as a module:

```python
subprocess.Popen([self._python, str(SANDBOX_PATH)], ...)
```

(`src/vtx/codemode/host.py:399-400`). `python -m vtx.codemode.sandbox` imports the parent package chain first  --  including the agent SDK  --  which costs roughly 1.5 seconds against about 30 ms for the by-path launch (`src/vtx/codemode/host.py:67`). The worker therefore imports nothing from `vtx`. It re-implements the error taxonomy locally so the same class identity can be reconstructed across the wire (`src/vtx/codemode/sandbox.py:241`).

The worker's `__name__` is `__codemode__` (`src/vtx/codemode/sandbox.py:264`), and it speaks newline-delimited JSON on fds 0 and 1. Before the script runs, `_reserve_fds` dups stdin to a protocol reader and stdout's fd to `_PROTOCOL_FD`, then points fds 0 and 1 at `/dev/null` so that stray `print` output or a child process writing to stdout cannot corrupt the frames (`src/vtx/codemode/sandbox.py:267-291`). Each frame is emitted with a single `os.write` (`src/vtx/codemode/sandbox.py:309`).

Your script is executed as the body of an async function. The source is indented and wrapped into `async def __codemode_main__()` (`src/vtx/codemode/sandbox.py:784-800`), which is what makes a top-level `return` legal. Three consequences:

- `return value` at the top level of the script sets `result.value`.
- Assigning to a variable named `result` does nothing; `result.value` is then `None`.
- Defining your own `async def runner()` does nothing either  --  only the script body's own return is read.

`sandbox.py` provides these globals to the script:

| Global | Value |
|---|---|
| `tools` | One proxy attribute per tool, plus `tools.search(query)` and `tools.describe(tool)` (`src/vtx/codemode/host.py:151`) |
| `store` | `(key, value)` tuple you assign to write the store, and read to get it |
| `load` | `get(key, default=None)` |
| `text` | Append plain text to the output captured for you |
| `image` | Return an image instead of text |
| `asyncio` | The real module, so scripts can gather concurrent calls |

The returned value must be JSON-safe: `coerce_json` rejects `NaN`/`Infinity` and non-string dict keys, and caps nesting at 32 levels (`src/vtx/codemode/jsonio.py:20-60`). Both host and worker import the same definition, so they cannot disagree.

## Limits and per-script options

`Limits` sets the defaults for every `execute()` (`src/vtx/codemode/types.py:141-183`):

| Field | Default | Meaning |
|---|---|---|
| `timeout_ms` | `30_000` | Inactivity deadline; no progress on any channel resets it |
| `wall_clock_ms` | `1_800_000` | Hard ceiling on total wall time |
| `max_tool_calls` | `None` | Cap on tool calls in one script |
| `max_output_tokens` | `None` | Cap on text output |
| `memory_limit_bytes` | `None` | Passed to the worker's resource limit |
| `detect_stalls` | `True` | Report a script that stops making progress as `stalled` |

The script can ask for less, never more, through an options line at the top of the source. It must be valid Python, and it is blanked rather than removed so traceback line numbers stay aligned with the source the model wrote (`src/vtx/codemode/source.py:106-107`). Three fields are recognized (`src/vtx/codemode/source.py:29`):

```python
# options: {"max_output_tokens": 4000, "max_tool_calls": 12, "timeout_ms": 60_000}
```

| Option | Bounds |
|---|---|
| `timeout_ms` | 1000 - 600000 (`src/vtx/codemode/source.py:32-33`) |
| `max_output_tokens` | 256 - 200000 (`src/vtx/codemode/source.py:38-39`) |
| `max_tool_calls` | 1 - 1000 (`src/vtx/codemode/source.py:43-44`) |

`clamp_timeout` and `clamp_int` apply the "min wins" rule against the host limit (`src/vtx/codemode/source.py:129-154`). Send the constraints to the model as a grammar with `CODEMODE_SOURCE_GRAMMAR` from `vtx.codemode.source` (`src/vtx/codemode/source.py:160-171`).

## The ToolRunner protocol

The sandbox never touches your tool classes. It hands you a `ToolRunner`-shaped callable  --  anything implementing:

```python
@runtime_checkable
class ToolRunner(Protocol):
    async def run_tool(self, name: str, args: dict[str, Any]) -> Any: ...
```

(`src/vtx/codemode/types.py:70-76`). Build one by wrapping the host's tool list with `governed_invoker` (`src/vtx/codemode/governance.py:177-209`). Tools themselves are described by `CodemodeTool` (`src/vtx/codemode/types.py:25-67`):

| Field | Meaning |
|---|---|
| `name` | Model-facing name, mapped to a Python attribute by `identifier()` (`src/vtx/codemode/types.py:57`) |
| `description`, `namespace_description` | Shown to the model in the catalog |
| `execute` | `async (args, signal) -> Any` |
| `input_schema`, `output_schema` | JSON Schema; `output_schema` lets the model see shape |
| `namespace` | Groups related tools under one attribute |
| `listed` | When `False`, reachable but not advertised by default |

Default catalog budget is 2000 tokens (`src/vtx/codemode/declarations.py:143`). If the harness already has `BaseTool` instances, `adapt_tools` converts them for you (`src/vtx/codemode/integration.py:165-194`); that is what the `codemode` tool does, so it does not re-declare anything (`src/vtx/agent/tools/codemode.py:188-189`).

## The governance gate

A script's tool calls must obey the same rules as the model's own. Before the change that introduced this gate, a tool called by a script went straight to `execute()` and skipped the extension hooks, the permission gate, and cost accounting  --  a single approved `codemode` call could do anything any exposed tool could (`src/vtx/codemode/governance.py:1-21`).

`ToolGovernance` wraps one tool and one dispatcher. Per call it runs three steps (`src/vtx/codemode/governance.py:64-119`):

1. **Permission gate**  --  call the host's `permission` callable, normally `vtx.core.permissions.check_permission` (`src/vtx/codemode/governance.py:192-194`). `ALLOW` runs the call. Any other answer, or a gate that raised, becomes a refusal (`src/vtx/codemode/governance.py:144-174`).
2. **Extension hooks**  --  emit `TOOL_EXECUTION_START` then `TOOL_CALL`. A handler may return `{"block": True}` with an optional `reason`, which raises `f"{name} was not run: {reason}"`, or `{"args": {...}}` to rewrite the arguments. A handler that rewrites arguments for the model must rewrite them for the script too (`src/vtx/codemode/governance.py:83-101`).
3. **Run the tool**, then announce `TOOL_EXECUTION_END`. Exceptions from that end-hook are swallowed  --  an extension's bug must not turn a tool that worked into a failure the script retries (`src/vtx/codemode/governance.py:124-142`).

`_decide`'s third case is the one that matters. `check_permission` answers ALLOW or PROMPT, and PROMPT means "ask the user"  --  which a script has nobody to ask. So PROMPT is treated as a refusal and the model is told to call the tool directly so the user can approve it (`src/vtx/codemode/governance.py:144-154`). A script is not a way to get a gated call done without asking; it is a way to do the ungated ones together (`src/vtx/codemode/governance.py:153-154`).

Anything the tool raises that is not a `ToolError` is wrapped as `f"{name} failed"` with the traceback kept in `detail`, not in the message: `detail` never crosses the wire into the model's context, because a traceback can carry paths and credentials (`src/vtx/codemode/governance.py:109-114`).

## Errors and diagnostics

`execute()` never raises for a script failure. Check `result.ok` and read `result.diagnostic` (`src/vtx/codemode/types.py:96-137`):

```python
result: Result   # ok, value, output, calls, diagnostic, store_writes, store_deletes, output_truncated
```

`Diagnostic` carries `kind`, `message`, and `stack`. The kinds are (`src/vtx/codemode/errors.py:63-85`):

| Kind | Raised as | Throwable in-script? |
|---|---|---|
| `unknown_tool` | `UnknownTool` | yes |
| `invalid_input` | `InvalidInput` | yes |
| `invalid_output` | `InvalidOutput` | yes |
| `host_unavailable` | `HostUnavailable` | yes |
| `tool_failure` | `ToolError` | yes |
| `script` | `ScriptError` | no |
| `timeout` | `ScriptTimeout` | no |
| `wall_clock` | `ScriptTimeout` | no |
| `aborted` | `ScriptAborted` | no |
| `stalled` | `ScriptStalled` | no |
| `sandbox` | `SandboxError` | no |

Catch the five `TOOL_KINDS` in your script with `try`/`except` to branch on a recoverable outcome; the six `SANDBOX_KINDS` are terminal and cannot be caught inside the script (`src/vtx/codemode/errors.py:79-85`). `remedy_for(kind)` returns the instruction text the model sees for each kind (`src/vtx/codemode/errors.py:140-142`).

## Worked example

Build a sandbox over two read-only tools, run a script that loops over files, and inspect the result. This is runnable as written:

```python
import asyncio
from pathlib import Path

from vtx.codemode import CodemodeSandbox, CodemodeTool, Limits


async def read_file(args, signal):
    with open(args["path"]) as fh:
        return fh.read()


async def word_count(args, signal):
    return len(args["text"].split())


async def main():
    sandbox = CodemodeSandbox(
        limits=Limits(timeout_ms=15_000, max_tool_calls=50),
        tools=[
            CodemodeTool(
                name="read-file",
                description="Read a file as text",
                execute=read_file,
                input_schema={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
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
    )

    async with sandbox:
        result = await sandbox.execute(
            """
from pathlib import Path

counts = {}
for path in sorted(Path(".").glob("*.md")):
    try:
        src = await tools.read_file(path=str(path))
        counts[path.name] = await tools.word_count(text=src)
    except UnknownTool as exc:
        text(f"missing tool: {exc}")

biggest = max(counts, key=counts.get) if counts else None
store("largest", biggest)
return {"counts": counts, "largest": biggest}
"""
        )

    if not result.ok:
        print(f"[{result.diagnostic.kind}] {result.diagnostic.message}")
        print(result.diagnostic.stack)
        return

    print(result.value)
    print("output:", result.output)
    print("calls:", [(c.name, c.duration_ms) for c in result.calls])
    for key, value in result.store_writes.items():
        print(f"store[{key}] = {value}")


asyncio.run(main())
```

Read `result.calls` for what the script actually ran: each `ToolCall` has `name`, `status`, `kind`, `message`, `duration_ms`, and a `.ok` property (`src/vtx/codemode/types.py:80-93`). Store writes are capped per value at 256 KiB and in total at 1 MiB (`src/vtx/codemode/types.py:19-22`); `result.apply_to_store(store)` merges them into a dict (`src/vtx/codemode/types.py:133-135`).

## Known gaps

- The module docstring in `src/vtx/codemode/sandbox.py:4` still says the host launches the worker as `python -I /path/to/sandbox.py`. The current host launches it with no `-I` (`src/vtx/codemode/host.py:399-400`). Trust the code.
- The sandbox imports nothing from `vtx`, so it cannot inspect the live session, agent, or tool registry. A script that needs those must ask for them through a tool you inject.
- Codemode cannot run against the interactive TUI; the harness path is a `BaseTool` with `mutating = True` (`src/vtx/agent/tools/codemode.py:188-189`), so its own call is subject to the permission gate like any other tool.

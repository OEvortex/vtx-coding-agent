"""Host side of the sandbox: spawn the process, run its tool calls, enforce limits.

One process per execution. The parent owns everything the script does not:
the deadline, the abort signal, and every tool. The script can only ask.

Two details are load-bearing:

**Tool calls are serialized on the parent's side.** The worker may have up to
eight calls in flight, so replies arrive interleaved. Each reply is matched by
id, and an unmatched or out-of-order frame is a protocol violation rather than
something to route optimistically.

**A timeout is a kill.** ``Process.kill`` (or ``terminate`` on Windows) ends
the interpreter outright, so ``while True:``, a runaway regex, and a C
extension that never returns all die the same way. There is no cooperative
cancellation to get wrong.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from vtx.ai.agent.codemode import errors, jsonio

# Frame types, mirrored as constants here rather than imported: the worker is
# launched by file path so it cannot import this package, and duplicating five
# string literals is cheaper than a shared module the worker would have to load
# through a path that reintroduces the import cost. tests/test_codemode.py
# asserts both sides agree.
_EXECUTE = "execute"
_TOOL_CALL = "tool_call"
_TOOL_RESULT = "tool_result"
_RESULT = "result"
from vtx.ai.agent.codemode.types import (
    MAX_STORE_TOTAL_CHARS,
    MAX_STORE_VALUE_CHARS,
    CodemodeTool,
    Diagnostic,
    Limits,
    Result,
    ToolCall,
    coerce_json,
)

#: Grace period between SIGTERM and SIGKILL. Long enough for a well-behaved
#: process to exit on its own, short enough that a wedged one is not the
#: user's problem.
_TERM_GRACE_SECONDS = 1.0

#: Windows has no SIGKILL; ``kill()`` maps to TerminateProcess there.
_IS_WINDOWS = os.name == "nt"

#: The sandbox worker, launched by path. See :meth:`CodemodeSandbox._spawn` for
#: why this is not ``python -m``.
SANDBOX_PATH = Path(__file__).with_name("sandbox.py")


class CodemodeSandbox:
    """A configured tool set plus its execution policy.

    Reusable: construct once, execute many times. Each
    :meth:`execute` is independent -- a fresh process, a fresh namespace --
    which is what makes the timeout a real boundary instead of a request the
    interpreter may decline.
    """

    def __init__(
        self,
        *,
        tools: Sequence[CodemodeTool] | None = None,
        limits: Limits | None = None,
        catalog_budget_tokens: int = 2000,
        python_executable: str | None = None,
    ) -> None:
        self._tools: tuple[CodemodeTool, ...] = tuple(tools or ())
        self._limits = limits or Limits()
        self._catalog_budget = catalog_budget_tokens
        self._python = python_executable or sys.executable
        self._closed = False
        #: Set by the deadline task so the read loop can tell a timeout from a
        #: crash. Reset per execution, so a slow run does not poison the next.
        self._timed_out = False
        self._aborted = False
        _reject_duplicate_identifiers(self._tools)

    @property
    def tools(self) -> tuple[CodemodeTool, ...]:
        return self._tools

    def declarations(self) -> str:
        """Render the model-facing tool list within the catalog budget."""
        from vtx.ai.agent.codemode.declarations import render_declarations

        text, _complete = render_declarations(self._tools, budget_tokens=self._catalog_budget)
        return text

    def instructions(self) -> str:
        """Render the full model-facing guide: workflow, rules, catalog.

        Ordered so the workflow is at the top and the catalog at the bottom.
        A model reads the first thing it sees and skips the rest, so the
        catalog being last is what keeps it from being read as instructions.
        """
        from vtx.ai.agent.codemode.declarations import render_declarations

        body, complete = render_declarations(self._tools, budget_tokens=self._catalog_budget)
        total = len(self._tools)
        shown = body.count("def tools.") if body else 0
        if complete:
            heading = f"## Available tools (COMPLETE list, {total} tools)"
        else:
            heading = f"## Available tools (PARTIAL - {shown} of {total} shown)"
            body = (
                f"{body}\n\n"
                f"{shown} of {total} tools are listed above. The rest are not; "
                "you must find them with `tools.search` before you can call them."
            ).strip()

        return f"""{_WORKFLOW}

{_RULES}

{_LANGUAGE}

{heading}

{body}"""

    async def execute(
        self,
        code: str,
        *,
        store: Mapping[str, Any] | None = None,
        timeout_ms: int | None = None,
        signal: asyncio.Event | None = None,
    ) -> Result:
        """Run one program and return its :class:`Result`.

        Never raises for script failure. ``Result.ok`` and
        ``Result.diagnostic`` are the outcome channel; an exception here means
        the host itself is broken, which is a different problem and should
        surface as one.
        """
        if self._closed:
            return _sandbox_failure("The sandbox is closed.")
        if not code.strip():
            return Result(ok=False, diagnostic=Diagnostic(errors.SCRIPT, "The script is empty."))

        deadline_ms = timeout_ms if timeout_ms is not None else self._limits.timeout_ms
        values = dict(store or {})
        rejections = _validate_store(values)
        if rejections:
            return Result(ok=False, diagnostic=Diagnostic(errors.SANDBOX, rejections))

        process = await self._spawn()
        if process is None:
            return _sandbox_failure("The sandbox process could not be started.")

        self._timed_out = False
        self._aborted = False
        try:
            return await self._pump(process, code, values, deadline_ms, signal)
        finally:
            await _reap(process)

    async def _spawn(self) -> subprocess.Popen[bytes] | None:
        """Start the worker.

        The worker is launched **by file path**, not with ``-m``. ``-m`` would
        import the parent package chain first -- and ``vtx/__init__`` pulls in
        the agent SDK, which costs ~1.5s on every single execution. Running the
        file directly skips all of it and lands near 30ms, which is why
        :mod:`vtx.ai.agent.codemode.sandbox` imports nothing from this package.
        """
        env = {
            **os.environ,
            # Inheriting an interactive PYTHONSTARTUP or sitecustomize would be
            # a way for the host environment to reach into the sandbox.
            "PYTHONNOUSERSITE": "1",
            "PYTHONHASHSEED": "0",
            "VTX_CODEMODE": "1",
        }
        try:
            return subprocess.Popen(  # noqa: S603 - fixed argv, no shell
                [self._python, "-I", str(SANDBOX_PATH)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=env,
                cwd=os.environ.get("VTX_CODEMODE_CWD") or None,
                bufsize=0,
                # Its own session, so the group-kill in _terminate cannot
                # reach the host. Without this the child shares the host's
                # process group and a timeout SIGTERM would kill the parent --
                # and anything else the user is running.
                start_new_session=True,
                # Windows has no process groups in the POSIX sense; this is the
                # closest equivalent and also prevents Ctrl-C in the console
                # from racing the sandbox's own lifecycle.
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                if _IS_WINDOWS
                else 0,
            )
        except OSError:
            return None

    async def _pump(
        self,
        process: subprocess.Popen[bytes],
        code: str,
        store: dict[str, Any],
        timeout_ms: int | None,
        signal: asyncio.Event | None,
    ) -> Result:
        """Drive the worker: send the request, answer tool calls, read the result."""
        assert process.stdin is not None
        assert process.stdout is not None

        request: dict[str, Any] = {
            "type": _EXECUTE,
            "code": code,
            "tools": [
                {"name": tool.name, "identifier": tool.identifier()} for tool in self._tools
            ],
            "store": store,
        }
        try:
            process.stdin.write((_dumps(request) + "\n").encode("utf-8"))
            process.stdin.flush()
        except (BrokenPipeError, OSError):
            return _sandbox_failure("The sandbox process closed its input before the script ran.")

        loop = asyncio.get_running_loop()
        calls: list[ToolCall] = []
        # Tool calls run as tasks, not inline. Awaiting each one before reading
        # the next frame serializes the host, which makes `asyncio.gather` in
        # the script buy nothing: the worker would send four calls, and the
        # parent would run them one at a time.
        inflight: dict[asyncio.Task[None], int] = {}
        timeout_task: asyncio.Task[None] | None = None
        abort_task: asyncio.Task[None] | None = None
        if timeout_ms is not None and timeout_ms > 0:
            timeout_task = asyncio.create_task(self._on_deadline(process, timeout_ms))
        if signal is not None:
            abort_task = asyncio.create_task(self._on_abort(process, signal))

        try:
            while True:
                frame = await self._read_frame(process)
                if frame is None:
                    return self._terminal_failure()
                kind = frame.get("type")
                if kind == _TOOL_CALL:
                    task = asyncio.create_task(self._serve_and_reply(process, frame, calls))
                    inflight[task] = int(frame.get("id") or 0)
                elif kind == _RESULT:
                    # Drain before reporting: a tool the script started and did
                    # not await still has a reply to write, and abandoning it
                    # would leave the tool's own side effects unaccounted for.
                    if inflight:
                        await asyncio.gather(*inflight, return_exceptions=True)
                    return _result_from_frame(frame)
                else:
                    # text() frames are already carried in the terminal frame's
                    # output list; acknowledging them here would be redundant.
                    continue
        except asyncio.CancelledError:
            await _terminate(process)
            raise
        finally:
            for task in (*inflight, timeout_task, abort_task):
                if task is not None:
                    task.cancel()
            pending = [task for task in (*inflight, timeout_task, abort_task) if task is not None]
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            del loop

    async def _serve_and_reply(
        self, process: subprocess.Popen[bytes], frame: Mapping[str, Any], calls: list[ToolCall]
    ) -> None:
        """Run one tool call and write its reply, as its own task."""
        assert process.stdin is not None
        reply = await self._serve(frame, calls)
        try:
            process.stdin.write((_dumps(reply) + "\n").encode("utf-8"))
            process.stdin.flush()
        except (BrokenPipeError, OSError):
            # The worker is gone; the read loop will notice and report. Swallowing
            # here is right because raising would discard the sibling tasks'
            # results too.
            return

    async def _read_frame(self, process: subprocess.Popen[bytes]) -> dict[str, Any] | None:
        """Read one newline-delimited frame from the worker's stdout.

        The read runs in a thread because it is a blocking pipe read. Doing it
        inline would stall the parent's event loop, and the loop is what serves
        the tool calls this same script is waiting on.
        """
        assert process.stdout is not None

        def _read() -> dict[str, Any] | None:
            return _read_frame(process.stdout)

        return await asyncio.get_running_loop().run_in_executor(None, _read)

    async def _serve(self, frame: Mapping[str, Any], calls: list[ToolCall]) -> dict[str, Any]:
        """Run one tool call and build its reply frame."""
        name = str(frame.get("name") or "")
        raw_args = frame.get("args")
        args = raw_args if isinstance(raw_args, dict) else {}
        call_id = int(frame.get("id") or 0)

        tool = next((t for t in self._tools if t.name == name), None)
        if tool is None:
            # The sandbox only exposes declared names, so this means the script
            # reached past the namespace -- or the two sides disagree, which is
            # a host defect. Either way it is not the script's input problem.
            error = errors.UnknownTool(name)
            calls.append(
                ToolCall(name=name, status="error", kind=error.kind, message=error.message)
            )
            return {
                "type": _TOOL_RESULT,
                "id": call_id,
                "ok": False,
                "value": None,
                "error": error.diagnostic(),
            }

        try:
            args = coerce_json(args, what=f"{name} arguments")
        except errors.ToolError as exc:
            calls.append(ToolCall(name=name, status="error", kind=exc.kind, message=exc.message))
            return {
                "type": _TOOL_RESULT,
                "id": call_id,
                "ok": False,
                "value": None,
                "error": exc.diagnostic(),
            }

        try:
            value = await tool.execute(args, None)
        except errors.ToolError as exc:
            calls.append(ToolCall(name=name, status="error", kind=exc.kind, message=exc.message))
            return {
                "type": _TOOL_RESULT,
                "id": call_id,
                "ok": False,
                "value": None,
                "error": exc.diagnostic(),
            }
        except Exception as exc:  # noqa: BLE001 - sanitized into a tool failure
            # Unclassified: the message is not forwarded. A tool that raises
            # something the host did not anticipate is a host defect, and its
            # text can carry paths or internals into the model's context.
            calls.append(ToolCall(name=name, status="error", kind=errors.TOOL_FAILURE))
            return {
                "type": _TOOL_RESULT,
                "id": call_id,
                "ok": False,
                "value": None,
                "error": {
                    "kind": errors.TOOL_FAILURE,
                    "message": errors.remedy_for(errors.TOOL_FAILURE),
                },
            }

        try:
            value = coerce_json(value, what=f"{name} result")
        except errors.ToolError as exc:
            calls.append(ToolCall(name=name, status="error", kind=exc.kind, message=exc.message))
            return {
                "type": _TOOL_RESULT,
                "id": call_id,
                "ok": False,
                "value": None,
                "error": exc.diagnostic(),
            }

        calls.append(ToolCall(name=name, status="ok"))
        return {"type": _TOOL_RESULT, "id": call_id, "ok": True, "value": value, "error": None}

    async def _on_deadline(self, process: subprocess.Popen[bytes], timeout_ms: int) -> None:
        await asyncio.sleep(timeout_ms / 1000)
        # Mark the deadline as reached before killing, so the read loop reports
        # the timeout rather than a generic "exited without returning".
        self._timed_out = True
        await _terminate(process)

    async def _on_abort(self, process: subprocess.Popen[bytes], signal: asyncio.Event) -> None:
        await signal.wait()
        self._aborted = True
        await _terminate(process)

    def _terminal_failure(self, detail: str | None = None) -> Result:
        """Classify a run that produced no result frame.

        A deadline or an abort is the *reason* the process is gone, so it is
        reported as such. Falling through to a generic sandbox failure would
        tell the model to retry identical code when the real problem was that
        it ran too long.
        """
        if self._timed_out:
            return Result(
                ok=False, diagnostic=Diagnostic(errors.TIMEOUT, errors.remedy_for(errors.TIMEOUT))
            )
        if self._aborted:
            return Result(
                ok=False, diagnostic=Diagnostic(errors.ABORTED, errors.remedy_for(errors.ABORTED))
            )
        return _sandbox_failure(detail or "The sandbox process exited without returning a result.")

    async def close(self) -> None:
        """Mark the sandbox closed. In-flight executions still finish."""
        self._closed = True

    async def __aenter__(self) -> CodemodeSandbox:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()


def _dumps(payload: Any) -> str:
    return jsonio.dumps(payload)


def _read_frame(stream: Any) -> dict[str, Any] | None:
    """Read one newline-delimited frame from a text stream, or ``None`` at EOF."""
    line = stream.readline()
    if not line:
        return None
    try:
        parsed = json.loads(line)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _result_from_frame(frame: Mapping[str, Any]) -> Result:
    """Project the worker's terminal frame into a :class:`Result`.

    Store writes are kept only on success. A script that half-ran and then
    raised must not leave the host holding state the model believes was never
    written.
    """
    calls = tuple(
        ToolCall(
            name=str(call.get("tool") or ""),
            status=str(call.get("status") or "error"),
            kind=call.get("kind"),
            message=call.get("message"),
        )
        for call in (frame.get("calls") or [])
        if isinstance(call, dict)
    )
    output = tuple(item for item in (frame.get("output") or []) if isinstance(item, dict))
    ok = bool(frame.get("ok"))

    if not ok:
        error = frame.get("error") or {}
        diagnostic = Diagnostic(
            kind=str(error.get("kind") or errors.SANDBOX),
            message=str(error.get("message") or errors.remedy_for(errors.SANDBOX)),
            stack=error.get("stack") if isinstance(error.get("stack"), str) else None,
        )
        return Result(ok=False, output=output, calls=calls, diagnostic=diagnostic)

    raw_writes = frame.get("store_writes")
    writes = dict(raw_writes) if isinstance(raw_writes, dict) else {}
    return Result(
        ok=True,
        value=frame.get("value"),
        output=output,
        calls=calls,
        store_writes=writes,
        store_deletes=frozenset(key for key, value in writes.items() if value is None),
    )


def _sandbox_failure(message: str) -> Result:
    return Result(ok=False, diagnostic=Diagnostic(errors.SANDBOX, message))


def _reject_duplicate_identifiers(tools: Sequence[CodemodeTool]) -> None:
    """Fail at construction if two tools would answer to one identifier.

    A collision would silently shadow one tool, and the shadowed one would look
    present in the declarations while being uncallable. Better to refuse the
    configuration than to debug that from a model's failed script.
    """
    seen: dict[str, str] = {}
    for tool in tools:
        identifier = tool.identifier()
        if identifier in seen:
            raise ValueError(
                f"tools {seen[identifier]!r} and {tool.name!r} both map to identifier {identifier!r}"
            )
        seen[identifier] = tool.name


def _validate_store(values: Mapping[str, Any]) -> str | None:
    """Reject a store that is already over budget before spending a process."""
    total = 0
    for key, value in values.items():
        try:
            encoded = jsonio.dumps(value)
        except (TypeError, ValueError):
            return f"store key {key!r} holds a value that is not JSON"
        if len(encoded) > MAX_STORE_VALUE_CHARS:
            return f"store key {key!r} exceeds {MAX_STORE_VALUE_CHARS} characters"
        total += len(encoded)
    if total > MAX_STORE_TOTAL_CHARS:
        return f"store exceeds {MAX_STORE_TOTAL_CHARS} characters"
    return None


async def _terminate(process: subprocess.Popen[bytes]) -> None:
    """End the worker: terminate the process tree, then force it."""
    if process.poll() is not None:
        return
    try:
        if _IS_WINDOWS:
            process.terminate()
        else:
            # Negative pid targets the process group, so a child the script
            # somehow spawned dies with it. The audit hook blocks that, but the
            # kill is the backstop for anything that slipped through.
            os.killpg(os.getpgid(process.pid), 15)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            process.kill()
        except OSError:
            return

    for _ in range(int(_TERM_GRACE_SECONDS * 20)):
        if process.poll() is not None:
            return
        await asyncio.sleep(0.05)

    try:
        if _IS_WINDOWS:
            process.kill()
        else:
            os.killpg(os.getpgid(process.pid), 9)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            process.kill()
        except OSError:
            pass


async def _reap(process: subprocess.Popen[bytes]) -> None:
    """Close the pipes and wait, so no zombie outlives the execution."""
    for stream in (process.stdin, process.stdout, process.stderr):
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass
    try:
        await asyncio.get_running_loop().run_in_executor(None, process.wait, 5.0)
    except (subprocess.TimeoutExpired, OSError):
        await _terminate(process)


_WORKFLOW = """## Workflow

1. Find the tool you need. If the tool list below is marked COMPLETE, pick from
   it. If it is marked PARTIAL, call `tools.search(query=..., limit=...)` first
   and read the signature it returns.
2. Call it by its exact name, as `tools.<identifier>(...)`. Arguments are
   keyword arguments; do not pass a positional dict.
3. `return` only the fields you need. The return value is what the model sees,
   so returning a whole API payload wastes the call you just saved.
"""

_RULES = """## Rules

- The only tools are the ones listed or returned by `tools.search`. There is no
  other way to reach a capability from inside the sandbox.
- Filter, sort, and aggregate collections in code. Do not make a tool call to
  compute something you can compute here.
- A tool's result may be `Any`. Narrow it at runtime before you use it, or the
  next line raises.
- Tools that are independent should be called together with
  `asyncio.gather(...)`, not one after another.
- Use `try`/`except` around a call that can legitimately fail, and branch on
  the exception type when the recovery differs. `UnknownTool` means the name
  is wrong; `InvalidInput` means the arguments are; `ToolError` means the tool
  ran and declined.
- `text(value)` appends to the output the model reads. `print` goes to a
  discarded stream and is not shown to the model -- use `text`.
- `store(key, value)` and `load(key)` carry JSON values between executions.
  Writes are committed only if the script returns successfully.
"""

_LANGUAGE = """## Language

This is a restricted Python environment. It has arithmetic, strings, lists,
dicts, sets, comprehensions, `for`/`while`, `try`/`except`, functions and
lambdas, `async`/`await`, and `import` for a small set of standard library
modules (`json`, `re`, `math`, `datetime`, `itertools`, `functools`,
`collections`, `textwrap`, `string`, `statistics`, and a few more).

It does not have: filesystem access, network access, subprocess execution,
`eval`/`exec`/`compile`, `open`, arbitrary imports, `os`, `sys`, or any
third-party package. Attempting one raises. If you need a capability that is
not in the tool list, it is not available -- say so rather than trying to work
around it.
"""

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
import contextlib
import json
import os
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from vtx.codemode import errors, jsonio
from vtx.codemode.declarations import rank as rank_tools
from vtx.codemode.declarations import render_signature
from vtx.codemode.errors import InvalidInput
from vtx.codemode.source import clamp_int, clamp_timeout
from vtx.codemode.types import (
    MAX_STORE_TOTAL_CHARS,
    MAX_STORE_VALUE_CHARS,
    CodemodeTool,
    Diagnostic,
    Limits,
    Result,
    ToolCall,
    coerce_json,
)

# Frame types, mirrored as constants here rather than imported: the worker is
# launched by file path so it cannot import this package, and duplicating five
# string literals is cheaper than a shared module the worker would have to load
# through a path that reintroduces the import cost. tests/test_codemode.py
# asserts both sides agree.
_EXECUTE = "execute"
_TOOL_CALL = "tool_call"
_TOOL_RESULT = "tool_result"
_RESULT = "result"

#: Grace period between SIGTERM and SIGKILL. Long enough for a well-behaved
#: process to exit on its own, short enough that a wedged one is not the
#: user's problem.
_TERM_GRACE_SECONDS = 1.0

#: Windows has no SIGKILL; ``kill()`` maps to TerminateProcess there.
_IS_WINDOWS = os.name == "nt"

#: The sandbox worker, launched by path. See :meth:`CodemodeSandbox._spawn` for
#: why this is not ``python -m``.
SANDBOX_PATH = Path(__file__).with_name("sandbox.py")

#: The tool the model calls to find tools the catalog budget could not inline.
#: Always registered, including when the catalog is complete, so a speculative
#: call never fails as an unknown tool -- and the instructions only advertise it
#: when the list really is partial.
#:
#: Named plainly because the model has to write it: ``tools.search(...)`` is
#: valid Python attribute access, and a ``$search`` spelling (the JavaScript
#: implementations' convention) would not be. A caller tool that wants this
#: name is rejected at construction rather than silently shadowed.
SEARCH_TOOL_NAME = "search"


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
        listed: Sequence[str] | None = None,
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
        #: Names the catalog advertises. ``None`` means all of them.
        #:
        #: A tool can be callable without being listed, and that is the whole
        #: point of the exposure taxonomy: with a large tool set, listing every
        #: callable tool would crowd the catalog past its budget and listing
        #: none of the MCP ones would make the integration useless. A tool the
        #: model can call but was not shown is still reachable by exact name or
        #: through ``tools.search``.
        self._listed = None if listed is None else frozenset(listed)
        _reject_duplicate_identifiers(self._tools)
        if any(tool.identifier() == SEARCH_TOOL_NAME for tool in self._tools):
            raise ValueError(
                f"{SEARCH_TOOL_NAME!r} is reserved for the sandbox's built-in tool "
                "search; rename the tool"
            )
        if self._listed is not None:
            unknown = self._listed - {t.name for t in self._tools}
            if unknown:
                raise ValueError(f"listed tools not in the sandbox: {', '.join(sorted(unknown))}")

    @property
    def tools(self) -> tuple[CodemodeTool, ...]:
        """The injected tools, not including the built-in search tool."""
        return self._tools

    @property
    def _advertised(self) -> tuple[CodemodeTool, ...]:
        """The tools the model-facing catalog describes."""
        if self._listed is None:
            return self._tools
        return tuple(tool for tool in self._tools if tool.name in self._listed)

    @property
    def _all_tools(self) -> tuple[CodemodeTool, ...]:
        """Every tool the sandbox serves, search included.

        Search is host-implemented rather than declared by the caller, so it is
        appended here instead of in the constructor: a host cannot accidentally
        shadow it, and it cannot be omitted by forgetting to pass it.
        """
        return (*self._tools, self._search_tool())

    def _search_tool(self) -> CodemodeTool:
        """The built-in tool-catalog search.

        Always present. The instructions only *advertise* it when the catalog is
        partial, but a speculative call from a model that misread the list should
        find the tool rather than fail as an unknown name -- otherwise the
        recovery advice ("search for it") is impossible to follow.

        It searches the whole callable set, not just what the catalog listed. A
        tool that is callable but unlisted is exactly the one a model has least
        reason to know exists, so hiding it from the search that exists to
        surface it would make it unreachable in practice.
        """

        tools = self._tools
        advertised = {tool.name for tool in self._advertised}

        async def search(args: dict[str, Any], _signal: Any) -> Any:
            query = args.get("query")
            namespace = args.get("namespace")
            limit = args.get("limit")
            offset = args.get("offset")
            if not isinstance(query, str):
                raise InvalidInput(SEARCH_TOOL_NAME, detail="query must be a string")
            if not isinstance(limit, int) or isinstance(limit, bool):
                limit = 10
            if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
                offset = 0
            pool = (
                [t for t in tools if (t.namespace or t.name.split(".", 1)[0]) == namespace]
                if isinstance(namespace, str)
                else list(tools)
            )
            # An exact path wins over a keyword match: a model that already knows
            # the name wants that tool, not the closest-ranked neighbour. The
            # accepted spellings are the ones the model could plausibly have
            # copied out of the catalog.
            exact = next(
                (
                    t
                    for t in pool
                    if t.name in (query, f"tools.{query}") or t.identifier() == query
                ),
                None,
            )
            if exact is not None:
                page: list[CodemodeTool] = [exact]
                remaining = 0
            elif not query.strip():
                # An empty query browses rather than matching nothing, which is
                # how a model that does not know what it is looking for can
                # enumerate a namespace.
                ordered = sorted(pool, key=lambda t: t.name)
                page = ordered[offset : offset + limit]
                remaining = max(0, len(ordered) - (offset + limit))
            else:
                ranked = rank_tools(query, pool, limit=offset + limit + 1)
                page = [match.tool for match in ranked[offset : offset + limit]]
                # `rank` returns at most `limit` matches, so a next page exists
                # exactly when it filled the requested window.
                remaining = max(0, len(ranked) - (offset + limit))
            return {
                "matches": [
                    {
                        "path": f"tools.{tool.identifier()}",
                        "name": tool.name,
                        "description": tool.description,
                        "signature": render_signature(tool),
                        # Whether the catalog named this one. A model that found
                        # a tool it was never shown benefits from knowing it is
                        # off-list rather than wondering why it is not above.
                        "listed": tool.name in advertised,
                    }
                    for tool in page
                ],
                "next": {"offset": offset + limit} if remaining > 0 else None,
                "remaining": remaining,
            }

        return CodemodeTool(
            name=SEARCH_TOOL_NAME,
            description=(
                "Search the tools this sandbox was given, including any the "
                "instructions did not list. Use it when the tool list is marked "
                "PARTIAL, or when you need a tool you were not shown."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What the tool does"},
                    "namespace": {
                        "type": "string",
                        "description": "Restrict to one top-level namespace",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Results to return",
                        "default": 10,
                    },
                    "offset": {"type": "integer", "description": "Skip this many matches"},
                },
                "required": ["query"],
            },
            execute=search,
        )

    def declarations(self) -> str:
        """Render the model-facing tool list within the catalog budget."""
        from vtx.codemode.declarations import render_declarations

        text, _complete = render_declarations(self._advertised, budget_tokens=self._catalog_budget)
        return text

    def instructions(self) -> str:
        """Render the full model-facing guide: workflow, rules, catalog.

        Ordered so the workflow is at the top and the catalog at the bottom.
        A model reads the first thing it sees and skips the rest, so the
        catalog being last is what keeps it from being read as instructions.

        The search tool is mentioned only when the catalog is genuinely
        partial. It is always *callable* -- a speculative call finds the tool
        rather than an unknown-name error -- but advertising a discovery step
        the model does not need costs it a turn for nothing.
        """
        from vtx.codemode.declarations import render_declarations

        advertised = self._advertised
        body, complete = render_declarations(advertised, budget_tokens=self._catalog_budget)
        total = len(advertised)
        shown = body.count("def tools.") if body else 0
        workflow = _WORKFLOW
        if complete:
            heading = f"## Available tools (COMPLETE list, {total} tools)"
        else:
            heading = f"## Available tools (PARTIAL - {shown} of {total} shown)"
            body = (
                f"{body}\n\n"
                f"{shown} of {total} tools are listed above. The rest are not; "
                f"find them with `tools.{SEARCH_TOOL_NAME}` before calling them."
            ).strip()
            workflow = _WORKFLOW_PARTIAL

        # A callable-but-unlisted tool is a different case from a listed one cut
        # by the budget, and needs a different sentence: the model was never
        # shown it, so "the list above is partial" does not explain its absence.
        unlisted = len(self._tools) - total
        if unlisted > 0:
            body = (
                f"{body}\n\n"
                f"{unlisted} further tool{'' if unlisted == 1 else 's'} can be "
                f"called but are not listed above. Find them with "
                f"`tools.{SEARCH_TOOL_NAME}`."
            ).strip()
            if complete:
                heading = f"## Available tools ({total} listed, {unlisted} findable)"
                workflow = _WORKFLOW_PARTIAL

        # The search mention belongs only to the partial case. In the complete
        # case it would advertise a discovery step the model does not need, and a
        # `COMPLETE` list is a claim the model should be able to act on without a
        # second lookup.
        rules = _RULES if complete and unlisted == 0 else _RULES_PARTIAL
        return f"""{workflow}

{rules}

{_LANGUAGE}

{heading}

{body}"""

    async def execute(
        self,
        code: str,
        *,
        store: Mapping[str, Any] | None = None,
        timeout_ms: int | None = None,
        max_tool_calls: int | None = None,
        max_output_tokens: int | None = None,
        signal: asyncio.Event | None = None,
    ) -> Result:
        """Run one program and return its :class:`Result`.

        Never raises for script failure. ``Result.ok`` and
        ``Result.diagnostic`` are the outcome channel; an exception here means
        the host itself is broken, which is a different problem and should
        surface as one.

        The three budget overrides are the script's own requests. Each is
        resolved against the sandbox's limit with min-wins, so a script can ask
        for less and never for more.
        """
        if self._closed:
            return _sandbox_failure("The sandbox is closed.")
        if not code.strip():
            return Result(ok=False, diagnostic=Diagnostic(errors.SCRIPT, "The script is empty."))

        deadline_ms = clamp_timeout(timeout_ms, self._limits.timeout_ms)
        call_budget = clamp_int(max_tool_calls, self._limits.max_tool_calls)
        output_budget = clamp_int(max_output_tokens, self._limits.max_output_tokens, floor=256)
        values = dict(store or {})
        rejections = _validate_store(values)
        if rejections:
            return Result(ok=False, diagnostic=Diagnostic(errors.SANDBOX, rejections))

        process = await self._spawn()
        if process is None:
            return _sandbox_failure("The sandbox process could not be started.")

        self._timed_out = False
        #: Distinguishes the two deadlines for the diagnostic. The wall clock is
        #: the one that fires while the script is blocked on a tool call, which
        #: is exactly the case the compute budget deliberately tolerates.
        self._wall_clock = False
        self._aborted = False
        try:
            return await self._pump(
                process,
                code,
                values,
                deadline_ms,
                call_budget,
                output_budget,
                signal,
                self._limits.wall_clock_ms,
            )
        finally:
            await _reap(process)

    async def _spawn(self) -> subprocess.Popen[bytes] | None:
        """Start the worker.

        The worker is launched **by file path**, not with ``-m``. ``-m`` would
        import the parent package chain first -- and ``vtx/__init__`` pulls in
        the agent SDK, which costs ~1.5s on every single execution. Running the
        file directly skips all of it and lands near 30ms, which is why
        :mod:`vtx.codemode.sandbox` imports nothing from this package.

        No ``-I`` and no ``PYTHONNOUSERSITE`` either. Both existed to seal the
        script off from the host's ``sitecustomize`` and ``PYTHONSTARTUP``; a
        script that can now import and run the whole installed environment needs
        site-packages on the path, and an inherited startup hook is no different
        from any other import.
        """
        env = {**os.environ, "PYTHONHASHSEED": "0", "VTX_CODEMODE": "1"}
        try:
            return subprocess.Popen(
                [self._python, str(SANDBOX_PATH)],
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
        call_budget: int | None,
        output_budget: int | None,
        signal: asyncio.Event | None,
        wall_clock_ms: int | None,
    ) -> Result:
        """Drive the worker: send the request, answer tool calls, read the result."""
        assert process.stdin is not None
        assert process.stdout is not None

        request: dict[str, Any] = {
            "type": _EXECUTE,
            "code": code,
            "tools": [
                {"name": tool.name, "identifier": tool.identifier()} for tool in self._all_tools
            ],
            "store": store,
            "detect_stalls": self._limits.detect_stalls,
            "memory_limit_bytes": self._limits.memory_limit_bytes,
        }
        try:
            process.stdin.write((_dumps(request) + "\n").encode("utf-8"))
            process.stdin.flush()
        except (BrokenPipeError, OSError):
            return _sandbox_failure("The sandbox process closed its input before the script ran.")

        state = _Execution(call_budget=call_budget, output_budget=output_budget)
        budget_task = (
            asyncio.create_task(self._on_budget(process, timeout_ms, state))
            if timeout_ms is not None and timeout_ms > 0
            else None
        )
        wall_task = (
            asyncio.create_task(self._on_wall_clock(process, wall_clock_ms))
            if wall_clock_ms is not None and wall_clock_ms > 0
            else None
        )
        # Tool calls run as tasks, not inline. Awaiting each one before reading
        # the next frame serializes the host, which makes `asyncio.gather` in
        # the script buy nothing: the worker would send four calls, and the
        # parent would run them one at a time.
        inflight: dict[asyncio.Task[None], int] = {}
        abort_task: asyncio.Task[None] | None = None
        if signal is not None:
            abort_task = asyncio.create_task(self._on_abort(process, signal))

        try:
            while True:
                frame = await self._read_frame(process)
                if frame is None:
                    return self._terminal_failure()
                kind = frame.get("type")
                if kind == _TOOL_CALL:
                    # Marked in flight before the task exists, so the compute
                    # budget cannot charge a slice between the frame arriving and
                    # the task starting. `leave_call` on completion is what
                    # resumes the clock, and it must fire for cancelled tasks too
                    # -- hence the callback rather than a line after the await.
                    state.enter_call()
                    task = asyncio.create_task(self._serve_and_reply(process, frame, state))
                    task.add_done_callback(lambda _: state.leave_call())
                    inflight[task] = int(frame.get("id") or 0)
                elif kind == _RESULT:
                    # Drain before reporting: a tool the script started and did
                    # not await still has a reply to write, and abandoning it
                    # would leave the tool's own side effects unaccounted for.
                    if inflight:
                        await asyncio.gather(*inflight, return_exceptions=True)
                    return _result_from_frame(frame, state, state.output_budget)
                else:
                    # text() frames are already carried in the terminal frame's
                    # output list; acknowledging them here would be redundant.
                    continue
        except asyncio.CancelledError:
            await _terminate(process)
            raise
        finally:
            # The clock tasks are cancelled as well. On the normal path the process has
            # already exited, and a budget task left running would sit against a
            # dead process for the rest of the wall clock.
            for task in (*inflight, budget_task, wall_task, abort_task):
                if task is not None:
                    task.cancel()
            pending = [t for t in (*inflight, budget_task, wall_task, abort_task) if t is not None]
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

    async def _serve_and_reply(
        self, process: subprocess.Popen[bytes], frame: Mapping[str, Any], state: _Execution
    ) -> None:
        """Run one tool call and write its reply, as its own task."""
        assert process.stdin is not None
        reply = await self._serve(frame, state)
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

    async def _serve(self, frame: Mapping[str, Any], state: _Execution) -> dict[str, Any]:
        """Run one tool call and build its reply frame."""
        name = str(frame.get("name") or "")
        raw_args = frame.get("args")
        args = raw_args if isinstance(raw_args, dict) else {}
        call_id = int(frame.get("id") or 0)

        def record(status: str, **fields: Any) -> None:
            state.by_id[call_id] = ToolCall(name=name, status=status, **fields)

        def reply(ok: bool, value: Any = None, error: Any = None) -> dict[str, Any]:
            return {"type": _TOOL_RESULT, "id": call_id, "ok": ok, "value": value, "error": error}

        def refuse(error: errors.ToolError) -> dict[str, Any]:
            record("error", kind=error.kind, message=error.message)
            return reply(False, error=error.diagnostic())

        # The budget is spent at admission, not at completion: four calls
        # already in flight have already been paid for, so a fifth refused
        # after four finish would let the script exceed the ceiling by exactly
        # the concurrency it asked for.
        refusal = state.admit()
        if refusal is not None:
            return refuse(refusal)

        tool = next((t for t in self._all_tools if t.name == name), None)
        if tool is None:
            # The sandbox only exposes declared names, so this means the script
            # reached past the namespace -- or the two sides disagree, which is
            # a host defect. Either way it is not the script's input problem.
            return refuse(errors.UnknownTool(name))

        try:
            # coerce_json is typed JsonValue, but a dict coerces to a dict; the
            # re-check keeps that guarantee visible to the type checker and
            # turns a host/sandbox disagreement into a refusal rather than a
            # call with the wrong argument shape.
            coerced = coerce_json(args, what=f"{name} arguments")
            if not isinstance(coerced, dict):
                return refuse(errors.InvalidOutput(f"{name} arguments"))
            args = coerced
        except errors.ToolError as exc:
            return refuse(exc)

        started = time.monotonic()
        try:
            value = await tool.execute(args, None)
        except errors.ToolError as exc:
            record("error", kind=exc.kind, message=exc.message, duration_ms=_elapsed_ms(started))
            return reply(False, error=exc.diagnostic())
        except Exception:
            # Unclassified: the message is not forwarded. A tool that raises
            # something the host did not anticipate is a host defect, and its
            # text can carry paths or internals into the model's context.
            record("error", kind=errors.TOOL_FAILURE, duration_ms=_elapsed_ms(started))
            return reply(
                False,
                error={
                    "kind": errors.TOOL_FAILURE,
                    "message": errors.remedy_for(errors.TOOL_FAILURE),
                },
            )

        try:
            value = coerce_json(value, what=f"{name} result")
        except errors.ToolError as exc:
            return refuse(exc)

        record("ok", duration_ms=_elapsed_ms(started))
        return reply(True, value=value)

    async def _on_budget(
        self, process: subprocess.Popen[bytes], timeout_ms: int, state: _Execution
    ) -> None:
        """Charge the script's own compute time against ``timeout_ms``.

        Time blocked on a tool call is not charged: the script is waiting on the
        host, and a `gather` over four sub-agents routinely waits minutes. What
        is left is the budget's actual subject -- a busy loop, a pathological
        regex, a runaway C extension -- with the wall-clock ceiling as backstop.
        """
        remaining_ms = float(timeout_ms)
        loop = asyncio.get_running_loop()
        while remaining_ms > 0:
            if state.blocked:
                await state.resumed.wait()
                continue
            started = loop.time()
            try:
                await asyncio.wait_for(state.suspended.wait(), remaining_ms / 1000)
            except TimeoutError:
                break
            remaining_ms -= (loop.time() - started) * 1000
        if self._timed_out:
            # The wall clock already fired and killed the process; there is no
            # budget left to report and no reason to signal a dead process again.
            return
        # Mark the deadline as reached before killing, so the read loop reports
        # the timeout rather than a generic "exited without returning".
        #
        # The kill happens immediately even with tool calls outstanding -- that is
        # the entire point of this deadline. Draining them first would turn a
        # spent budget back into an unbounded wait, which is the failure the
        # wall clock exists to catch.
        self._timed_out = True
        await _terminate(process)

    async def _on_wall_clock(self, process: subprocess.Popen[bytes], wall_clock_ms: int) -> None:
        """The absolute ceiling: tool time included, nothing pauses it.

        This is what keeps a tool that never returns from holding the process
        open, which is the one failure the compute budget deliberately tolerates.
        """
        await asyncio.sleep(wall_clock_ms / 1000)
        if self._timed_out:
            return
        self._timed_out = True
        self._wall_clock = True
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
            # The two deadlines need different next moves: an exhausted compute
            # budget means the script's own work was too much, while the wall
            # clock means something it was waiting on never came back.
            kind = errors.WALL_CLOCK if self._wall_clock else errors.TIMEOUT
            return Result(ok=False, diagnostic=Diagnostic(kind, errors.remedy_for(kind)))
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


class _Execution:
    """Mutable per-run state, shared by every tool-call task.

    One object rather than loose parameters because the call counter has to be
    read-modify-written from several concurrent tasks; passing an ``int`` would
    lose the increments.
    """

    __slots__ = (
        "by_id",
        "call_budget",
        "output_budget",
        "outstanding",
        "resumed",
        "spent",
        "suspended",
    )

    def __init__(self, *, call_budget: int | None, output_budget: int | None) -> None:
        self.call_budget = call_budget
        self.output_budget = output_budget
        self.spent = 0
        #: Tool calls admitted but not yet replied. Nonzero means the script is
        #: blocked on the host rather than running, which is what lets the compute
        #: budget distinguish "slow" from "waiting".
        self.outstanding = 0
        #: Set while ``outstanding`` is nonzero; ``resumed`` is its inverse.
        #: Both are per-execution state, so a deadline task can wait on either
        #: without coordinating with the tool-call tasks beyond the counter.
        self.suspended = asyncio.Event()
        self.resumed = asyncio.Event()
        self.resumed.set()
        #: Protocol id -> the host's record of that call, in completion order.
        #: Keyed by id rather than appended to a list because concurrent calls
        #: finish out of order, and the id is the only thing both sides agree on.
        self.by_id: dict[int, ToolCall] = {}

    @property
    def blocked(self) -> bool:
        return self.outstanding > 0

    def enter_call(self) -> None:
        """Mark a tool call as in flight, so the compute budget pauses."""
        self.outstanding += 1
        self.suspended.set()
        self.resumed.clear()

    def leave_call(self) -> None:
        """Mark a tool call as answered. The last one resumes the clock."""
        self.outstanding = max(0, self.outstanding - 1)
        if not self.outstanding:
            self.suspended.clear()
            self.resumed.set()

    def admit(self) -> errors.ToolError | None:
        """Claim one call against the budget, or refuse it.

        A refusal comes back as a tool error rather than an execution failure so
        the script can catch it and adapt -- fan out less, or ask the user. A
        script that cannot see why it stopped is a script that retries.
        """
        self.spent += 1
        if self.call_budget is not None and self.spent > self.call_budget:
            return errors.ToolError(
                f"This script may make at most {self.call_budget} tool call"
                f"{'' if self.call_budget == 1 else 's'}, and has reached that. "
                "Do less per call, filter before fetching more, or split the "
                "work across several codemode calls.",
                kind=errors.TOOL_FAILURE,
            )
        return None


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.monotonic() - started) * 1000))


def _result_from_frame(
    frame: Mapping[str, Any], state: _Execution, output_budget: int | None = None
) -> Result:
    """Project the worker's terminal frame into a :class:`Result`.

    Store writes are kept only on success. A script that half-ran and then
    raised must not leave the host holding state the model believes was never
    written.

    The host's own record of each call is authoritative for the name, the status,
    and the duration -- only it knows when a tool actually ran. The worker's
    record is joined on for the failure kind and message, which it alone knows,
    because a call the *script* caught reports differently from one the host
    refused. The join is on the protocol id rather than on position: with
    ``asyncio.gather`` the two sides finish in different orders, so pairing them
    by index would attribute a call's error to its neighbour.
    """
    by_id = {
        int(call["id"]): call
        for call in (frame.get("calls") or [])
        if isinstance(call, dict) and isinstance(call.get("id"), int)
    }
    calls = tuple(
        ToolCall(
            name=recorded.name,
            status=recorded.status,
            kind=by_id.get(call_id, {}).get("kind") if call_id is not None else None,
            message=by_id.get(call_id, {}).get("message") if call_id is not None else None,
            duration_ms=recorded.duration_ms,
        )
        for call_id, recorded in state.by_id.items()
    )
    output, truncated = _cap_output(
        tuple(item for item in (frame.get("output") or []) if isinstance(item, dict)),
        output_budget,
    )
    ok = bool(frame.get("ok"))

    if not ok:
        error = frame.get("error") or {}
        diagnostic = Diagnostic(
            kind=str(error.get("kind") or errors.SANDBOX),
            message=str(error.get("message") or errors.remedy_for(errors.SANDBOX)),
            stack=error.get("stack") if isinstance(error.get("stack"), str) else None,
        )
        return Result(
            ok=False, output=output, calls=calls, diagnostic=diagnostic, output_truncated=truncated
        )

    raw_writes = frame.get("store_writes")
    writes = dict(raw_writes) if isinstance(raw_writes, dict) else {}
    return Result(
        ok=True,
        value=frame.get("value"),
        output=output,
        calls=calls,
        store_writes=writes,
        store_deletes=frozenset(key for key, value in writes.items() if value is None),
        output_truncated=truncated,
    )


#: Characters per token when estimating output cost. The same heuristic the
#: catalog budget uses, so one number governs both.
CHARS_PER_TOKEN = 4


def _cap_output(
    output: tuple[Mapping[str, Any], ...], budget_tokens: int | None
) -> tuple[tuple[Mapping[str, Any], ...], bool]:
    """Cut the middle out of a script's text output to fit the budget.

    Head and tail are kept because both carry meaning: the head is what the
    script was doing, the tail is what it concluded. Images are never dropped --
    a truncated picture is a broken picture, and their cost is bounded by the
    call budget rather than by length.

    The returned *value* is not capped here. It arrives already decoded, so
    there is nothing to shorten without a second serialization round trip; the
    caller that renders it to text applies :func:`truncate_middle` instead.
    """
    if budget_tokens is None or not output:
        return output, False
    allowance = budget_tokens * CHARS_PER_TOKEN
    if sum(len(str(item.get("text") or "")) for item in output) <= allowance:
        return output, False

    kept: list[Mapping[str, Any]] = []
    spent = 0
    for item in output:
        if item.get("type") != "text":
            kept.append(item)
            continue
        text = str(item.get("text") or "")
        room = allowance - spent
        if room <= 0:
            continue
        if len(text) <= room:
            kept.append(item)
            spent += len(text)
            continue
        # The marker is paid for out of the same allowance rather than added on
        # top of it. Measuring only the retained text would overshoot the budget
        # by the length of the notice explaining the overshoot -- which nobody
        # notices until the budget is what is keeping a session inside its
        # context window.
        head_room, tail_room, marker = _split_within(text, room)
        removed = len(text) - head_room - tail_room
        marker = f"\n... {removed:,} characters truncated ...\n"
        kept.append({**item, "text": text[:head_room] + marker + text[-tail_room:]})
        spent = allowance
    return tuple(kept), True


def _split_within(text: str, room: int) -> tuple[int, int, str]:
    """Split ``room`` characters into head and tail, leaving room for the marker.

    The marker names how much was dropped, so its own length depends on the split
    and the split depends on the marker's length. Two passes converge: the second
    one has the right digit count, and a marker that grows by a character is
    absorbed by the retained text rather than the budget.
    """
    kept = room
    for _ in range(2):
        marker = f"\n... {max(0, len(text) - kept):,} characters truncated ...\n"
        kept = max(0, room - len(marker))
    head_room = kept // 2
    return head_room, kept - head_room, marker


def truncate_middle(text: str, budget_tokens: int) -> tuple[str, bool]:
    """Shorten rendered text to a token budget, keeping both ends.

    Used for the value a script returned, which reaches the model as text the
    caller assembles. Returns ``(text, truncated)`` so the caller can tell the
    model it was cut -- a silently shortened result reads as a complete one,
    which is the failure this whole mechanism exists to prevent.
    """
    allowance = budget_tokens * CHARS_PER_TOKEN
    if len(text) <= allowance:
        return text, False
    head_room, tail_room, marker = _split_within(text, allowance)
    removed = len(text) - head_room - tail_room
    marker = f"\n... {removed:,} characters truncated ...\n"
    return text[:head_room] + marker + text[-tail_room:], True


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
                f"tools {seen[identifier]!r} and {tool.name!r} both map to "
                f"identifier {identifier!r}"
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

    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        if _IS_WINDOWS:
            process.kill()
        else:
            os.killpg(os.getpgid(process.pid), 9)
        return
    # The group signal failed (already reaped, or no group). Fall back to the
    # single process; failing to kill a process that already exited is fine.
    with contextlib.suppress(OSError):
        process.kill()


async def _reap(process: subprocess.Popen[bytes]) -> None:
    """Close the pipes and wait, so no zombie outlives the execution."""
    for stream in (process.stdin, process.stdout, process.stderr):
        if stream is not None:
            with contextlib.suppress(OSError):
                stream.close()
    try:
        await asyncio.get_running_loop().run_in_executor(None, process.wait, 5.0)
    except (subprocess.TimeoutExpired, OSError):
        await _terminate(process)


_WORKFLOW = """## Workflow

1. Find the tool you need in the list below, which is marked COMPLETE.
2. Call it by its exact name, as `tools.<identifier>(...)`. Arguments are
   keyword arguments; do not pass a positional dict.
3. `return` only the fields you need. The return value is what the model sees,
   so returning a whole API payload wastes the call you just saved.
"""

#: Same workflow, for a catalog the budget could not fit whole. The difference
#: is step 1: the list is a subset, so the model has to search before it can
#: assume a capability is absent.
_WORKFLOW_PARTIAL = f"""## Workflow

1. Find the tool you need. The list below is marked PARTIAL and does not show
   everything, so call `tools.{SEARCH_TOOL_NAME}(query=..., limit=...)` first and
   read the signature it returns. Never conclude a capability is missing just
   because it is not listed.
2. Call it by its exact name, as `tools.<identifier>(...)`. Arguments are
   keyword arguments; do not pass a positional dict.
3. `return` only the fields you need. The return value is what the model sees,
   so returning a whole API payload wastes the call you just saved.
"""

_RULES_BODY = """
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
- `image(value)` appends an image the model can see. It takes a base64 data
  URI, `{"image_url": ...}`, or an image block straight out of an MCP result --
  so a tool that returns a picture can be forwarded without re-encoding it.
  A remote `http` URL is refused.
- `store(key, value)` and `load(key)` carry JSON values between executions.
  Writes are committed only if the script returns successfully.
- The first line may be `# @options: {"timeout_ms": 5000, "max_tool_calls": 20,
  "max_output_tokens": 4000}` to lower your own budgets. Every field is
  optional. You may only ask for *less* than the host allows; asking for more
  is clamped, and an unknown field is an error.
"""

_RULES = "## Rules" + _RULES_BODY

#: Same rules, plus the one the partial case needs: how to find a tool that is
#: not listed. Without it, a model reading a subset would conclude the missing
#: capability does not exist -- which is the exact wrong conclusion.
_RULES_PARTIAL = f"""## Rules

- The only tools are the ones listed below or returned by
  `tools.{SEARCH_TOOL_NAME}`. There is no other way to reach a capability from
  inside the sandbox, so search before concluding something is unavailable.
{_RULES_BODY}"""

_LANGUAGE = """## Language

This is real Python. The whole standard library is available, third-party
packages are installed and importable, and `open`, `os`, `subprocess` and the
network all work — the script runs as the same user, in the same directory,
with the same permissions as any command you would run yourself. Use them.

Two things are different. First, the running agent is not reachable from inside
the script: reaching a tool means calling `tools.<name>(...)`, because the tool
set and session state live in the parent process. Second, `print` and anything
else written to stdout or stderr is discarded, so `text(value)` is the only
output the model reads.
"""

"""The script worker. Self-contained, and launched by file path.

This module is the whole worker, and it deliberately imports **nothing** from
``vtx``. The host launches it as ``python -I /path/to/sandbox.py`` rather than
``python -m vtx...``, because ``-m`` imports the parent package chain first --
``vtx/__init__`` pulls in the agent SDK, which costs ~1.5s per execution.
Running the file directly skips all of that and lands at ~30ms.

That constraint is why the protocol constants and diagnostic kinds appear twice
in this repository: once here, once in :mod:`vtx.codemode.errors`.
They are a wire contract between two processes, so duplication is the honest
shape -- and ``tests/test_codemode.py`` asserts the two lists match, which is
what keeps the duplication from drifting.

**The script is real Python.** Full builtins, full stdlib, third-party packages,
filesystem, subprocesses, network -- all of it, with the user's own privileges.
This is the ``ref/prime-agent`` model: a coding agent driving a Python
interpreter on the developer's own machine, where restricting the interpreter
buys nothing the user asked for and costs the model every library it might have
used. The ``vtx`` package stays importable from a script -- it is installed --
so this file's own source is readable from one. What does not cross is the
host's live state: the running agent, the session, and every tool.

**The process boundary is what enforces that,** and it is load-bearing for one
specific reason: a deadline must be a ``kill``, not a cooperative request. So
``while True:``, a pathological regex, a blocking ``read()``, and a runaway C
extension all die the same way. Running the script in the host would give up
that, and nothing else here replaced it.

**Two file descriptors are reserved before the script runs** (see
:func:`_reserve_fds`). The worker speaks newline-delimited JSON on fds 0 and 1,
and a script with real ``sys.stdout`` and real ``subprocess`` inherits both --
``print``, ``os.write(1, ...)`` and any child process all write to fd 1. Without
the reservation, one long ``print`` line lands in the middle of a frame and the
host reports a transport failure for a script that ran fine.
"""

from __future__ import annotations

import asyncio
import json
import os
import queue
import sys
import threading
import traceback
from textwrap import indent
from typing import Any

# --------------------------------------------------------------------------
# Wire protocol. Mirrored in vtx/ai/agent/codemode/protocol.py.
# --------------------------------------------------------------------------

EXECUTE = "execute"
TOOL_RESULT = "tool_result"
TOOL_CALL = "tool_call"
TEXT = "text"
RESULT = "result"

#: Diagnostic kinds. Mirrored in vtx/ai/agent/codemode/errors.py -- keep in sync.
SCRIPT = "script"
TIMEOUT = "timeout"
#: The absolute wall-clock ceiling. The worker never raises this -- the host
#: kills the process and reports it -- but it is mirrored so a kind the model
#: can be shown is a kind the worker knows the name of.
WALL_CLOCK = "wall_clock"
ABORTED = "aborted"
SANDBOX = "sandbox"
UNKNOWN_TOOL = "unknown_tool"
INVALID_INPUT = "invalid_input"
TOOL_FAILURE = "tool_failure"
INVALID_OUTPUT = "invalid_output"
HOST_UNAVAILABLE = "host_unavailable"
#: The script is blocked on something that can never complete. Distinct from
#: TIMEOUT because the cause is provable in a millisecond rather than waited
#: for, and because the fix is a different edit.
STALLED = "stalled"

KINDS = (
    SCRIPT,
    TIMEOUT,
    WALL_CLOCK,
    ABORTED,
    SANDBOX,
    STALLED,
    UNKNOWN_TOOL,
    INVALID_INPUT,
    TOOL_FAILURE,
    INVALID_OUTPUT,
    HOST_UNAVAILABLE,
)

#: Recovery hints, keyed by kind. The category exists to change behaviour, not
#: just to label.
_REMEDY = {
    SCRIPT: (
        "The script raised or failed to compile. The traceback carries a line "
        "number in your own source; fix that line and run again."
    ),
    TIMEOUT: (
        "The script spent its whole compute budget and was killed. Waiting on tool "
        "calls does not count against that budget -- this is your own work taking "
        "too long. Do less of it: fetch less, split the job across calls, or "
        "filter in steps rather than one pass."
    ),
    WALL_CLOCK: (
        "The run hit its absolute time limit and was killed. A call you were "
        "waiting on never came back. Call the slow tool directly instead of "
        "wrapping it in a script, or ask for less of it."
    ),
    ABORTED: "The run was cancelled. Nothing was written to the store.",
    SANDBOX: (
        "The sandbox process failed, not your script. Retry once; if it persists, "
        "report it rather than rewriting working code."
    ),
    STALLED: (
        "The script is waiting on a result that can never arrive: no tool call is "
        "outstanding and no timer can fire. You awaited something that will never "
        "settle -- await a tool call, or stop awaiting."
    ),
    UNKNOWN_TOOL: (
        "That tool is not in the sandbox. Check the declared tools for the exact "
        "name; tool names are exposed as identifiers with non-identifier "
        "characters replaced by underscore."
    ),
    INVALID_INPUT: (
        "The tool was found but the arguments were wrong. Re-read its declared "
        "parameters and call it again with a corrected payload; do not retry the "
        "identical call."
    ),
    TOOL_FAILURE: (
        "The tool ran and declined. Read what it said and change your approach; "
        "repeating the same call will fail the same way."
    ),
    INVALID_OUTPUT: (
        "The tool produced a result that cannot be returned as JSON. Narrow the "
        "call (fewer records, a summary, a filter) so the value is plain data."
    ),
    HOST_UNAVAILABLE: (
        "The host bridge is not wired up for this session. Work with what the "
        "declarations expose instead."
    ),
}


def remedy_for(kind: str) -> str:
    return _REMEDY.get(kind, _REMEDY[SANDBOX])


# --------------------------------------------------------------------------
# Exceptions. These are the classes the script catches by name.
# --------------------------------------------------------------------------


class CodemodeError(Exception):
    kind = SANDBOX

    def diagnostic(self) -> dict[str, Any]:
        return {"kind": self.kind, "message": remedy_for(self.kind)}


class ToolError(CodemodeError):
    """A tool's failure. Only ``message`` crosses the boundary; ``detail`` does not."""

    def __init__(
        self, message: str, *, detail: str | None = None, kind: str = TOOL_FAILURE
    ) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail
        self.kind = kind

    def diagnostic(self) -> dict[str, Any]:
        return {"kind": self.kind, "message": self.message}


class UnknownTool(ToolError):  # noqa: N818 - see above
    def __init__(self, name: str) -> None:
        super().__init__(f"Unknown tool: {name}", kind=UNKNOWN_TOOL)


class InvalidInput(ToolError):  # noqa: N818 - see above
    def __init__(self, name: str, *, detail: str | None = None) -> None:
        super().__init__(f"Invalid arguments for tool: {name}", detail=detail, kind=INVALID_INPUT)


class InvalidOutput(ToolError):  # noqa: N818 - see above
    def __init__(self, what: str, *, detail: str | None = None) -> None:
        super().__init__(
            f"{what} is not JSON data: {detail or 'no JSON representation'}",
            detail=detail,
            kind=INVALID_OUTPUT,
        )


class HostUnavailable(ToolError):  # noqa: N818 - see above
    def __init__(self, detail: str | None = None) -> None:
        super().__init__(
            "The host bridge is not available in this session",
            detail=detail,
            kind=HOST_UNAVAILABLE,
        )


class ScriptError(CodemodeError):
    kind = SCRIPT

    def __init__(self, message: str, *, stack: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.stack = stack

    def diagnostic(self) -> dict[str, Any]:
        return {"kind": SCRIPT, "message": self.message, "stack": self.stack}


class SandboxError(CodemodeError):
    pass


class ScriptAborted(CodemodeError):  # noqa: N818 - see above
    kind = ABORTED


class ScriptStalled(CodemodeError):  # noqa: N818 - see above
    """Raised when the script is provably unable to make further progress."""

    kind = STALLED

    def diagnostic(self) -> dict[str, Any]:
        return {"kind": STALLED, "message": remedy_for(STALLED), "stack": None}


class ScriptTimeout(CodemodeError):  # noqa: N818 - see above
    kind = TIMEOUT


# Mirrors the host-side justification for the same names in errors.py: a model
# script writes `except UnknownTool:`, so the short form is the contract.


def _raise_tool_error(kind: str, message: str) -> BaseException:
    """Rebuild the typed exception for a kind so the script can branch on it.

    Python gets this and JavaScript does not: the script can write
    ``except UnknownTool:`` and get exactly that branch, because the class
    identity survives the round trip.
    """
    if kind == UNKNOWN_TOOL:
        return UnknownTool(message)
    if kind == INVALID_INPUT:
        return InvalidInput(message)
    if kind == INVALID_OUTPUT:
        return InvalidOutput(message)
    if kind == HOST_UNAVAILABLE:
        return HostUnavailable()
    if kind == TIMEOUT:
        return ScriptTimeout()
    if kind == ABORTED:
        return ScriptAborted()
    return ToolError(message, kind=TOOL_FAILURE)


#: The ``__name__`` the script's namespace carries.
_SCRIPT_MODULE = "__codemode__"


def _reserve_fds() -> Any:
    """Hand the protocol a private fd 0, then point fds 0 and 1 at /dev/null.

    The worker and the script share a process, and a script with real
    ``sys.stdout``, real ``subprocess`` and real ``open`` reaches both inherited
    descriptors. Left alone, that corrupts the wire format in two directions:

    - **fd 1.** ``print``, ``sys.stdout.write``, ``os.write(1, ...)`` and every
      child process land in the middle of a JSON frame. The host parses it as a
      malformed line and reports a transport failure for a script that ran
      correctly. Output goes through ``text()``, which writes to the private
      fd; everything else is discarded, exactly as the instructions say.
    - **fd 0.** The tool bridge's reader thread owns this one. A script calling
      ``input()`` would take a tool reply off the wire and leave the call that
      was waiting for it blocked forever. /dev/null makes ``input()`` see EOF,
      which is the ordinary Python answer for a closed input.

    The returned stream is the reader's own copy, so only it can reach the
    protocol.
    """
    protocol_in = os.fdopen(os.dup(0), "r")
    protocol_out = os.dup(1)
    os.set_inheritable(protocol_out, False)
    devnull = os.open(os.devnull, os.O_RDWR)
    os.dup2(devnull, 0)
    os.dup2(devnull, 1)
    os.close(devnull)
    global _PROTOCOL_FD
    _PROTOCOL_FD = protocol_out
    return protocol_in


# --------------------------------------------------------------------------
# Frames
# --------------------------------------------------------------------------


#: The write end of the protocol. Defaults to fd 1 and is swapped for a private
#: dup by :func:`_reserve_fds` before the script runs.
_PROTOCOL_FD = 1


def write_frame(payload: dict[str, Any]) -> None:
    """Write one frame as a single line to the protocol.

    A single ``os.write`` matters: the script may ``print`` at any moment, and
    Python's buffered stdout is not under our control. One syscall per frame
    keeps frames atomic with respect to each other, which is what lets the
    parent parse whole lines.
    """
    data = (json.dumps(payload, ensure_ascii=False, default=str) + "\n").encode("utf-8")
    os.write(_PROTOCOL_FD, data)


def read_frame(stream: Any) -> dict[str, Any] | None:
    """Read one frame from a text stream, or ``None`` at end of stream."""
    line = stream.readline()
    if not line:
        return None
    try:
        parsed = json.loads(line)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------


class _CallResult:
    """One tool call's outcome, as the worker saw it."""

    __slots__ = ("call_id", "kind", "message", "name", "status", "value")

    def __init__(
        self,
        name: str,
        status: str,
        value: Any = None,
        kind: str | None = None,
        message: str | None = None,
        call_id: int | None = None,
    ) -> None:
        self.name = name
        self.status = status
        self.value = value
        self.kind = kind
        self.message = message
        #: The protocol id, so the host can join this record to the one it timed.
        #: Joining by position would be wrong: with `asyncio.gather` the two sides
        #: finish in different orders.
        self.call_id = call_id

    def as_frame(self) -> dict[str, Any]:
        frame: dict[str, Any] = {"tool": self.name, "status": self.status}
        if self.call_id is not None:
            frame["id"] = self.call_id
        if self.kind is not None:
            frame["kind"] = self.kind
        if self.message is not None:
            frame["message"] = self.message
        return frame


#: Fixed cap. An interpreter property rather than a host knob: it bounds the
#: worker's own threads, and a host raising it buys nothing it cannot get from a
#: second, independent execution.
MAX_CONCURRENT_TOOL_CALLS = 8

#: Deepest tool-call nesting served. Guards a script that calls a tool from
#: inside a hook it also controls.
_MAX_CALL_DEPTH = 16


class _ToolBridge:
    """Serves ``tools.<name>(args)`` by round-tripping to the host.

    Concurrency needs one reader, not one per call. Each call thread doing its
    own blocking ``readline`` on the same stdin makes four concurrent calls
    compete for four replies, and three of them get the wrong one -- which
    surfaces as mismatched-reply errors rather than as a slowdown. So a single
    dispatcher thread owns the read side and routes each reply to the queue its
    call is waiting on.
    """

    def __init__(self, stream: Any) -> None:
        self._lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(MAX_CONCURRENT_TOOL_CALLS)
        self._next_id = 0
        self._calls: list[_CallResult] = []
        # Per-thread: a depth guard shared across threads would make one
        # concurrent call count against another's budget.
        self._depth = threading.local()
        self._pending: dict[int, queue.SimpleQueue[Any]] = {}
        self._closed = False
        self._stream = stream
        self._reader = threading.Thread(
            target=self._read_loop, name="codemode-reader", daemon=True
        )
        self._reader.start()

    def _read_loop(self) -> None:
        while True:
            frame = read_frame(self._stream)
            with self._lock:
                closed = self._closed
            if closed:
                return
            if frame is None:
                # EOF: wake every waiter so none blocks forever.
                with self._lock:
                    waiting = list(self._pending.values())
                    self._pending.clear()
                for inbox in waiting:
                    inbox.put(None)
                return
            call_id = frame.get("id")
            with self._lock:
                inbox = self._pending.get(call_id) if isinstance(call_id, int) else None
            if inbox is not None:
                inbox.put(frame)

    @property
    def calls(self) -> list[_CallResult]:
        with self._lock:
            return list(self._calls)

    @property
    def in_flight(self) -> int:
        """How many tool calls the host owes a reply to.

        The stall detector's premise: a reply arriving is one of only two things
        that can resume a blocked script, so a script that is blocked while
        this is zero is blocked on nothing reachable.
        """
        with self._lock:
            return len(self._pending)

    def make_proxy(self, identifier: str, declared: str) -> Any:
        # The round trip blocks on the parent's reply, so it happens off the
        # event loop thread. Waiting inline would park the loop for the
        # duration of the call and `asyncio.gather` would run the calls one
        # after another -- the concurrency would exist on paper and nowhere
        # else.
        async def call(**kwargs: Any) -> Any:
            depth = getattr(self._depth, "value", 0)
            if depth >= _MAX_CALL_DEPTH:
                raise InvalidInput(declared, detail="tool calls nested too deeply")
            return await asyncio.to_thread(self._dispatch, declared, kwargs)

        call.__name__ = identifier
        return call

    def _dispatch(self, declared: str, args: dict[str, Any]) -> Any:
        inbox: queue.SimpleQueue[Any] = queue.SimpleQueue()
        with self._lock:
            self._next_id += 1
            call_id = self._next_id
            self._pending[call_id] = inbox
        self._depth.value = getattr(self._depth, "value", 0) + 1
        try:
            write_frame({"type": TOOL_CALL, "id": call_id, "name": declared, "args": _plain(args)})
            reply = inbox.get()
            if reply is None:
                self._record(
                    declared, "error", kind=ABORTED, message=remedy_for(ABORTED), call_id=call_id
                )
                raise ScriptAborted()
            if reply.get("type") != TOOL_RESULT or reply.get("id") != call_id:
                self._record(declared, "error", kind=SANDBOX, call_id=call_id)
                raise SandboxError("The host sent a mismatched tool reply.")
            if reply.get("ok"):
                value = reply.get("value")
                self._record(declared, "ok", value=value, call_id=call_id)
                return value
            error = reply.get("error") or {}
            kind = str(error.get("kind") or TOOL_FAILURE)
            message = str(error.get("message") or remedy_for(kind))
            self._record(declared, "error", kind=kind, message=message, call_id=call_id)
            raise _raise_tool_error(kind, message)
        finally:
            self._depth.value -= 1
            with self._lock:
                self._pending.pop(call_id, None)

    def _record(
        self,
        name: str,
        status: str,
        value: Any = None,
        kind: str | None = None,
        message: str | None = None,
        call_id: int | None = None,
    ) -> None:
        with self._lock:
            self._calls.append(_CallResult(name, status, value, kind, message, call_id))

    def shutdown(self) -> None:
        """Stop the reader and release anyone still waiting."""
        with self._lock:
            self._closed = True
            waiting = list(self._pending.values())
            self._pending.clear()
        for inbox in waiting:
            inbox.put(None)


class _Tools:
    """The ``tools`` namespace, raising a typed error for an unknown name.

    A plain dict answers ``tools.echo`` with
    ``AttributeError: 'dict' object has no attribute 'echo'`` -- a statement
    about the container, not about the model's chosen name, and it lands as an
    unclassified script error. Raising :class:`UnknownTool` instead tells the
    model the tool does not exist *and* lets a handler branch on that.

    An explicit proxy rather than a dict subclass: attribute access on an
    instance is resolved against the class first, and a subclass would have to
    raise a non-``AttributeError`` from ``__getattr__`` to be useful here.
    """

    def __init__(self, tools: dict[str, Any]) -> None:
        self._tools = tools

    def __getattr__(self, name: str) -> Any:
        tool = self._tools.get(name)
        if tool is None:
            raise UnknownTool(name)
        return tool

    def __getitem__(self, name: str) -> Any:
        tool = self._tools.get(name)
        if tool is None:
            raise UnknownTool(name)
        return tool

    def __contains__(self, name: object) -> bool:
        # Membership stays honest: `if "x" in tools` is how a script checks
        # before calling, and it has to agree with what a call does.
        return name in self._tools

    def __iter__(self) -> Any:
        return iter(self._tools)

    def __len__(self) -> int:
        return len(self._tools)

    def __dir__(self) -> list[str]:
        # Without this, enumeration shows the five dunders and no tools -- so a
        # script that lists first sees nothing to call.
        return [*super().__dir__(), *self._tools]

    def keys(self) -> Any:
        """Every available tool name."""
        return self._tools.keys()

    def items(self) -> Any:
        return self._tools.items()

    def get(self, name: str, default: Any = None) -> Any:
        """Non-raising lookup."""
        return self._tools.get(name, default)

    def __repr__(self) -> str:
        return f"<tools {sorted(self._tools)}>"


class _Store:
    """The script's view of ``store``/``load``.

    Reads come from a copy so mutating a loaded value cannot reach host state.
    Writes are staged and reported, never applied here -- the host decides
    whether to commit, which is why a failed run leaves nothing behind.
    """

    def __init__(self, values: dict[str, Any]) -> None:
        self._values = dict(values)
        self.writes: dict[str, Any] = {}
        self.deletes: set[str] = set()

    def get(self, key: str, default: Any = None) -> Any:
        if key in self.writes:
            return self.writes[key]
        if key in self.deletes:
            return default
        value = self._values.get(key, default)
        return _copy(value)

    def set(self, key: str, value: Any) -> None:
        # Storing None deletes the key. Distinct from omitting a write: a delete
        # is an instruction the host has to apply, not an absence.
        if value is None:
            self.deletes.add(key)
            self.writes.pop(key, None)
            return
        self.deletes.discard(key)
        self.writes[key] = _plain(value)


def _copy(value: Any) -> Any:
    """Round-trip through JSON so the script cannot mutate host state."""
    try:
        return json.loads(json.dumps(value, default=str))
    except (TypeError, ValueError):
        return value


def _plain(value: Any) -> Any:
    """Coerce into JSON-safe data, failing loudly when it cannot."""
    try:
        json.dumps(value)
    except (TypeError, ValueError) as exc:
        raise InvalidOutput("arguments", detail=str(exc)) from exc
    return value


#: What ``image()`` accepts, in the order it tries them. An MCP tool result can
#: be handed over untouched, which is the point: a script that calls a
#: screenshot tool and forwards the block should not have to know the encoding.
_IMAGE_EXPECTS = (
    "image() expects a non-empty image URL string, an object with 'image_url', "
    "or a raw MCP image block"
)

#: Only inline data is accepted. A remote URL would make the fetch the host's
#: problem at render time, on a URL the model chose, with the model's own
#: reachability assumptions -- and a script that could point the transcript at
#: an arbitrary host is an exfiltration primitive dressed as a feature.
_IMAGE_REMOTE = "remote image URLs are not supported; pass a base64 data URI instead"


def _image_payload(value: Any) -> tuple[str, str]:
    """Return ``(base64 data, mime type)`` for whatever ``image()`` was handed.

    Accepts a data URI, ``{"image_url": ...}``, and an MCP ``ImageContent``
    block. Raises :class:`TypeError` on anything else, naming the three shapes
    rather than saying "invalid argument" -- a model that guessed wrong should
    not have to guess again.
    """
    url: Any = value
    if isinstance(value, dict):
        kind = value.get("type")
        if kind == "image":
            data = value.get("data")
            mime = value.get("mimeType") or value.get("mime_type")
            if not isinstance(data, str) or not data:
                raise TypeError("image() expected MCP image block data")
            return data, mime if isinstance(mime, str) and mime else "application/octet-stream"
        if kind is not None:
            raise TypeError(f"image() only accepts MCP image blocks, got {kind!r}")
        if "image_url" in value:
            url = value["image_url"]
    if not isinstance(url, str) or not url:
        raise TypeError(_IMAGE_EXPECTS)

    colon = url.find(":")
    scheme = url[:colon].lower() if colon != -1 else ""
    if scheme in ("http", "https"):
        raise TypeError(_IMAGE_REMOTE)
    comma = url.find(",")
    header = [p.strip().lower() for p in url[colon + 1 : comma].split(";")] if comma != -1 else []
    if scheme != "data" or comma == -1 or "base64" not in header[1:]:
        raise TypeError("invalid image output; pass a base64 data URI")
    return url[comma + 1 :], header[0] or "application/octet-stream"


def _install_exception_types(namespace: dict[str, Any]) -> None:
    """Expose the typed error classes so a script can catch them by name.

    Without this the script would have to inspect ``exc.args[0]`` to tell a bad
    tool name from a tool that declined, which is exactly the recovery
    information the taxonomy exists to preserve.
    """
    for name in (
        "ToolError",
        "UnknownTool",
        "InvalidInput",
        "InvalidOutput",
        "HostUnavailable",
        "ScriptError",
        "ScriptTimeout",
        "ScriptAborted",
        "ScriptStalled",
        "SandboxError",
    ):
        namespace[name] = globals()[name]


def _pending_timer_count(loop: Any) -> int | None:
    """How many timer callbacks the loop still owes, or ``None`` if unknowable.

    ``asyncio.sleep`` is available to scripts here, unlike in the JavaScript
    reference where the VM has no timers at all. So a script waiting on a sleep
    is waiting on a real thing, and a naive "blocked and no tool calls" check
    would call that a deadlock. Timers are the difference between the two, and
    they live on a private attribute, so the answer is reported as unknowable
    rather than guessed when the internals move -- which disables the check
    rather than firing it wrongly.
    """
    try:
        return len(loop._scheduled)
    except Exception:
        return None


def _other_live_tasks(loop: Any, mine: set[asyncio.Task[Any]]) -> list[asyncio.Task[Any]]:
    """Tasks other than the script's and ours that could still do work.

    A task the script spawned is a producer of whatever the script is waiting
    for, so its presence means "not provably stuck". Counting them is the
    difference between a proof and a guess.
    """
    try:
        return [t for t in asyncio.all_tasks(loop) if t not in mine and not t.done()]
    except Exception:
        # Unknowable: return something that suppresses the check.
        return [loop]  # type: ignore[list-item]


class _StallGuard:
    """Fails a script that provably cannot make progress.

    The sandbox has exactly two ways to resume a blocked script: a tool reply
    arriving, or a timer firing. A script that is blocked with neither pending
    is not slow, it is dead -- so the run can end in a millisecond with a
    diagnosis, instead of sitting out the whole deadline and then reporting a
    timeout, which would send the model off splitting its work when the actual
    bug is a promise that will never settle.
    """

    def __init__(self, task: asyncio.Task[Any], bridge: Any) -> None:
        self._task = task
        self._bridge = bridge
        self.stalled = False

    async def watch(self) -> None:
        loop = asyncio.get_running_loop()
        mine = {t for t in (self._task, asyncio.current_task()) if t is not None}
        while not self._task.done():
            # Yield so anything runnable runs first. A task that merely needed
            # another turn must not be mistaken for a blocked one.
            await asyncio.sleep(0)
            if self._task.done():
                return
            if self._bridge.in_flight:
                continue
            timers = _pending_timer_count(loop)
            if timers is None or timers:
                continue
            if _other_live_tasks(loop, mine):
                continue
            self.stalled = True
            self._task.cancel()
            return


async def _run_script(coro: Any, bridge: Any, *, detect_stalls: bool) -> Any:
    """Await the script's entry point, watching for a provable deadlock."""
    task = asyncio.ensure_future(coro)
    if not detect_stalls:
        return await task
    guard = _StallGuard(task, bridge)
    watcher = asyncio.ensure_future(guard.watch())
    try:
        return await task
    except asyncio.CancelledError:
        # Only ours if the guard raised the flag; otherwise this is a cancellation
        # from elsewhere and it belongs to the caller.
        if guard.stalled:
            raise ScriptStalled from None
        raise
    finally:
        watcher.cancel()


def _exec_script(code: str, namespace: dict[str, Any], bridge: Any, *, detect_stalls: bool) -> Any:
    """Run ``code`` as an async function body and return its result.

    The source is wrapped rather than compiled as a bare ``exec`` block so
    top-level ``return`` works, which is what the model expects when told it is
    writing a script body. A bare block would make the body a module, where
    ``return`` is a syntax error.
    """
    body = indent(code, "    ")
    compiled = compile(
        f"async def __codemode_main__():\n{body or '    pass'}\n", "<codemode>", "exec"
    )
    exec(compiled, namespace)
    main = namespace.get("__codemode_main__")
    if main is None:
        return None

    async def runner() -> Any:
        return await _run_script(main(), bridge, detect_stalls=detect_stalls)

    return asyncio.run(runner())


def _script_diagnostic(exc: BaseException) -> dict[str, Any]:
    """Turn an uncaught script exception into a model-facing diagnostic.

    Tracebacks are rebuilt from the script's own frames only: sandbox frames
    are host internals and would leak paths into context, while the script's
    frames carry the line number that lets the model fix the code.
    """
    if isinstance(exc, CodemodeError):
        diagnostic = exc.diagnostic()
        diagnostic.setdefault("stack", None)
        return diagnostic
    if isinstance(exc, KeyboardInterrupt):
        return {"kind": ABORTED, "message": remedy_for(ABORTED), "stack": None}
    # A `PermissionError` or `ImportError` here is an ordinary script error now
    # that nothing is denying it: the model picked a path it cannot read or a
    # package that is not installed. It gets a stack and a line number like any
    # other, because that is what tells the model what to change.
    return {"kind": SCRIPT, "message": f"{type(exc).__name__}: {exc}", "stack": _script_stack(exc)}


def _script_stack(exc: BaseException) -> str:
    frames = traceback.extract_tb(exc.__traceback__)
    script_frames = [f for f in frames if f.filename == "<codemode>"]
    if not script_frames:
        return ""
    lines = traceback.format_list(script_frames)
    return f"Traceback (most recent call last):\n{''.join(lines)}{type(exc).__name__}: {exc}"


def _limit_memory(limit_bytes: int | None) -> None:
    """Cap this process's address space, if asked and if the platform allows.

    A runaway allocation is otherwise only stopped by the deadline, so the user
    watches memory climb for the whole of it. ``RLIMIT_AS`` is checked for
    rather than assumed: it is absent on Windows, and the import itself is
    conditional for the same reason. A missing limit degrades to the deadline,
    which is the pre-existing behaviour.
    """
    if not limit_bytes or limit_bytes <= 0:
        return
    try:
        import resource
    except ImportError:
        return
    try:
        _soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        ceiling = limit_bytes if hard == resource.RLIM_INFINITY else min(limit_bytes, hard)
        resource.setrlimit(resource.RLIMIT_AS, (ceiling, hard))
    except (ValueError, OSError, AttributeError):
        return


def run(request: dict[str, Any], *, stdin: Any = None) -> dict[str, Any]:
    """Execute one program and return the terminal ``result`` frame."""
    code = str(request.get("code") or "")
    declarations = request.get("tools") or []
    initial_store = request.get("store") or {}
    detect_stalls = request.get("detect_stalls", True) is not False

    _limit_memory(request.get("memory_limit_bytes"))
    bridge = _ToolBridge(stdin if stdin is not None else sys.stdin)
    store = _Store(dict(initial_store))
    output: list[dict[str, Any]] = []

    def text(value: Any = "") -> None:
        """Append an output item. Non-strings are JSON-encoded."""
        rendered = (
            value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
        )
        output.append({"type": "text", "text": rendered})
        write_frame({"type": TEXT, "value": rendered})

    def image(value: Any) -> None:
        """Append an image to the output the model sees.

        The reason this exists: a tool that returns a picture -- a screenshot, a
        chart, a rendered page -- currently has it flattened into text by the
        time a script sees it, and a script that cannot forward the image has no
        way to pass it on. Taking a raw MCP image block makes the common case
        a pass-through.
        """
        data, mime = _image_payload(value)
        output.append({"type": "image", "data": data, "mimeType": mime})

    proxies = {
        d["identifier"]: bridge.make_proxy(d["identifier"], d["name"])
        for d in declarations
        if isinstance(d.get("identifier"), str) and isinstance(d.get("name"), str)
    }

    namespace: dict[str, Any] = {
        "__name__": _SCRIPT_MODULE,
        "tools": _Tools(proxies),
        "store": store.set,
        "load": store.get,
        "text": text,
        "image": image,
        # The real module. It was a six-name facade only to keep the script away
        # from the loop policy and the default executor, which confinement no
        # longer needs; a script that can `import asyncio` anyway has no reason
        # to be handed a thinner one.
        "asyncio": asyncio,
    }
    _install_exception_types(namespace)

    value: Any = None
    error: dict[str, Any] | None = None
    try:
        value = _exec_script(code, namespace, bridge, detect_stalls=detect_stalls)
    except BaseException as exc:
        error = _script_diagnostic(exc)

    # Release the reader before the process exits, so a blocked read thread
    # cannot keep it alive past the terminal frame.
    bridge.shutdown()

    return {
        "type": RESULT,
        "ok": error is None,
        "value": value,
        "store_writes": dict(store.writes),
        "calls": [c.as_frame() for c in bridge.calls],
        "output": output,
        "error": error,
    }


def main() -> int:
    """Read one execution request on stdin, write one result frame on stdout."""
    # Before anything else: the script is about to get both fds, and the
    # protocol needs to keep them.
    protocol_in = _reserve_fds()
    line = protocol_in.readline()
    if not line:
        return 1
    try:
        request = json.loads(line)
    except json.JSONDecodeError:
        return 1

    write_frame(run(request, stdin=protocol_in))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException as exc:
        # A worker that cannot start must still answer, or the parent blocks on
        # a read that never comes and reports a crash instead of the real
        # problem.
        write_frame(
            {
                "type": RESULT,
                "ok": False,
                "value": None,
                "store_writes": {},
                "calls": [],
                "output": [],
                "error": {
                    "kind": SANDBOX,
                    "message": f"The sandbox failed to start: {type(exc).__name__}: {exc}",
                    "stack": None,
                },
            }
        )
        raise SystemExit(1) from exc

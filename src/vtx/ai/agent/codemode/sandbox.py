"""The sandbox process. Self-contained, and launched by file path.

This module is the whole worker, and it deliberately imports **nothing** from
``vtx``. The host launches it as ``python -I /path/to/sandbox.py`` rather than
``python -m vtx...``, because ``-m`` imports the parent package chain first --
``vtx/__init__`` pulls in the agent SDK, which costs ~1.5s per execution.
Running the file directly skips all of that and lands at ~30ms.

That constraint is why the protocol constants and diagnostic kinds appear twice
in this repository: once here, once in :mod:`vtx.ai.agent.codemode.errors`.
They are a wire contract between two processes, so duplication is the honest
shape -- and ``tests/test_codemode.py`` asserts the two lists match, which is
what keeps the duplication from drifting.

**The isolation story** is three layers, because CPython has no wasm boundary:

1. **A separate process.** The script cannot reach host memory. A deadline is a
   ``kill``, not a cooperative request, so ``while True:``, a pathological
   regex, and a runaway C extension all die the same way.
2. **``sys.addaudithook``.** Installed before the script is compiled. Denies
   ``open``, the filesystem-discovery events, every route to a second process,
   sockets, ``ctypes``, ``marshal``, and the guard's own removal. Auditing is a
   CPython-level event, so it fires however the operation is reached.
3. **A bare namespace.** ``__builtins__`` is replaced with an allowlist, which
   removes ``eval``, ``exec``, ``compile``, ``open``, ``getattr``, ``globals``
   and ``__import__`` in one move rather than by enumeration.

**The object graph is reachable, and that is accepted.** A script can walk
``().__class__.__base__.__subclasses__()`` to a class whose
``__init__.__globals__`` holds ``os`` or ``subprocess``. That is a known CPython
introspection surface and it cannot be closed from Python.

It is not a hole, because the audit hook is the authority boundary and it fires
on the *operation*, not on how the object was reached. Verified against the
walked graph: ``subprocess.run``/``Popen``/``os.popen`` all raise ``open``;
``_socket.socket().connect()`` raises ``socket.connect``; ``os.open``+``os.read``
raises ``open``; ``os.fork`` raises ``os.fork``; ``ctypes`` and
``_posixsubprocess`` are not resident at all.

What does leak is metadata that raises no audit event: ``os.stat``,
``os.access``, ``os.readlink``, ``os.getcwd``, ``os.uname``, ``os.environ``.
A script can confirm that a path it already knows exists and read its size.
No contents, no execution, no network. Closing that needs a syscall filter
(seccomp), which a portable implementation cannot assume.
"""

from __future__ import annotations

import asyncio
import builtins
import json
import os
import queue
import sys
import threading
import traceback
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
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
ABORTED = "aborted"
SANDBOX = "sandbox"
UNKNOWN_TOOL = "unknown_tool"
INVALID_INPUT = "invalid_input"
TOOL_FAILURE = "tool_failure"
INVALID_OUTPUT = "invalid_output"
HOST_UNAVAILABLE = "host_unavailable"

KINDS = (
    SCRIPT,
    TIMEOUT,
    ABORTED,
    SANDBOX,
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
        "The deadline expired and the process was killed. Narrow the work: fetch "
        "less, split the job across calls, or filter in steps rather than one pass."
    ),
    ABORTED: "The run was cancelled. Nothing was written to the store.",
    SANDBOX: (
        "The sandbox process failed, not your script. Retry once; if it persists, "
        "report it rather than rewriting working code."
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


class UnknownTool(ToolError):
    def __init__(self, name: str) -> None:
        super().__init__(f"Unknown tool: {name}", kind=UNKNOWN_TOOL)


class InvalidInput(ToolError):
    def __init__(self, name: str, *, detail: str | None = None) -> None:
        super().__init__(f"Invalid arguments for tool: {name}", detail=detail, kind=INVALID_INPUT)


class InvalidOutput(ToolError):
    def __init__(self, what: str, *, detail: str | None = None) -> None:
        super().__init__(
            f"{what} is not JSON data: {detail or 'no JSON representation'}",
            detail=detail,
            kind=INVALID_OUTPUT,
        )


class HostUnavailable(ToolError):
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


class ScriptAborted(CodemodeError):
    kind = ABORTED


class ScriptTimeout(CodemodeError):
    kind = TIMEOUT


class SandboxViolation(RuntimeError):
    """Raised by the audit hook when the script reaches for denied authority."""


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


# --------------------------------------------------------------------------
# Confinement
# --------------------------------------------------------------------------

#: Modules the script may import. Leaf maths/text modules, plus ``urllib.parse``
#: for URL handling. Deliberately tiny: anything with an import-time side effect
#: is out.
ALLOWED_IMPORTS = frozenset(
    {
        "collections",
        "collections.abc",
        "dataclasses",
        "datetime",
        "decimal",
        "difflib",
        "enum",
        "functools",
        "itertools",
        "json",
        "math",
        "operator",
        "re",
        "statistics",
        "string",
        "textwrap",
        "types",
        "unicodedata",
        "urllib.parse",
    }
)

#: Builtins the script may call. Everything with a side effect is absent.
#:
#: Note what is missing and why: ``eval``/``exec``/``compile`` would defeat the
#: interpreter boundary, ``__import__`` is replaced by the guard below,
#: ``getattr``/``setattr``/``globals``/``vars`` reach dunders and therefore the
#: object graph, ``open`` is filesystem authority the host never grants, and
#: ``input``/``breakpoint`` block on a terminal the sandbox does not have.
#: ``dir`` is absent too, which would otherwise enumerate the whole object
#: graph -- ``tools.keys()`` is the supported way to list tools.
ALLOWED_BUILTINS = (
    "abs",
    "all",
    "any",
    "ascii",
    "bin",
    "bool",
    "bytes",
    "callable",
    "chr",
    "complex",
    "dict",
    "divmod",
    "enumerate",
    "filter",
    "float",
    "format",
    "frozenset",
    "hash",
    "hex",
    "int",
    "isinstance",
    "issubclass",
    "iter",
    "len",
    "list",
    "map",
    "max",
    "min",
    "next",
    "oct",
    "ord",
    "pow",
    "print",
    "range",
    "repr",
    "reversed",
    "round",
    "set",
    "slice",
    "sorted",
    "str",
    "sum",
    "tuple",
    "type",
    "zip",
    # Exception types. Without these the script cannot write
    # ``except ValueError:`` -- every bare ``except`` still works, but the
    # taxonomy is only useful if the script can name the branch it wants.
    "ArithmeticError",
    "AssertionError",
    "AttributeError",
    "BaseException",
    "Exception",
    "IndexError",
    "KeyError",
    "LookupError",
    "MemoryError",
    "NameError",
    "OverflowError",
    "RecursionError",
    "RuntimeError",
    "StopIteration",
    "SyntaxError",
    "TypeError",
    "ValueError",
    "ZeroDivisionError",
)

_FS = "filesystem access is not available; use the tools"
_EXEC = "process execution is not available; use the tools"
_NATIVE = "native code loading is not available"
_NET = "network access is not available; use the tools"

#: Audit events that grant authority, and so are denied.
#:
#: The list is derived from what CPython actually raises, not from what looks
#: dangerous -- an event that is never raised defends nothing, and a missing one
#: is a hole. Notable groupings:
#:
#: - **Filesystem.** ``open`` covers contents; ``os.listdir``, ``os.scandir``,
#:   ``os.walk`` and ``glob.glob`` cover *discovery*, which is the more useful
#:   half -- an enumeration leaks the shape of the host.
#: - **Execution.** Every route to a second process, including those reached
#:   through ``os`` rather than ``subprocess``.
#: - **Native code.** ``ctypes`` and ``marshal``: the two ways to turn bytes
#:   into something that runs.
#: - **The guard itself.** ``sys.addaudithook`` and ``sys.settrace`` are denied
#:   because a script that could add a hook or install a tracer could remove or
#:   blind this one.
#:
#: Known gaps, stated rather than papered over: ``os.stat``, ``os.access`` and
#: ``os.readlink`` raise no audit event, so a script that reaches ``os`` can
#: confirm a path it already knows exists and read its metadata. Information
#: leak, not authority.
_DENIED_EVENTS = {
    # Filesystem: contents.
    "open": _FS,
    # Filesystem: discovery.
    "os.listdir": _FS,
    "os.scandir": _FS,
    "os.walk": _FS,
    "glob.glob": _FS,
    "glob.glob/2": _FS,
    "os.remove": _FS,
    "os.rename": _FS,
    "os.mkdir": _FS,
    "os.rmdir": _FS,
    "os.truncate": _FS,
    "os.link": _FS,
    "os.symlink": _FS,
    "os.chmod": _FS,
    "os.chown": _FS,
    "tempfile.mkstemp": _FS,
    "tempfile.mkdtemp": _FS,
    "shutil.copyfile": _FS,
    "shutil.move": _FS,
    "shutil.rmtree": _FS,
    # Process execution.
    "subprocess.Popen": _EXEC,
    "os.system": _EXEC,
    "os.spawn": _EXEC,
    "os.fork": _EXEC,
    "os.forkpty": _EXEC,
    "os.posix_spawn": _EXEC,
    "os.exec": _EXEC,
    "os.startfile": _EXEC,
    "pty.spawn": _EXEC,
    # Network.
    "socket.connect": _NET,
    "socket.bind": _NET,
    "socket.getaddrinfo": _NET,
    "socket.gethostbyname": _NET,
    "socket.sendto": _NET,
    "urllib.Request": _NET,
    "ftplib.connect": _NET,
    "http.client.connect": _NET,
    # Native code and deserialization: bytes in, running code out.
    "ctypes.dlopen": _NATIVE,
    "ctypes.dlsym": _NATIVE,
    "ctypes.call_function": _NATIVE,
    "ctypes.set_exception": _NATIVE,
    "ctypes.create_string_buffer": _NATIVE,
    "marshal.loads": "runtime code compilation is not available",
    "code.__new__": "runtime code compilation is not available",
    "pickle.find_class": "deserialization is not available",
    # Interpreter introspection that would defeat the guard.
    "sys.addaudithook": "the audit hook cannot be modified",
    "sys.setprofile": "tracing cannot be installed",
    "sys.settrace": "tracing cannot be installed",
    "sys._getframe": "frame introspection is not available",
    "gc.get_objects": "runtime introspection is not available",
    "sys.set_asyncgen_hooks": "runtime hooks cannot be installed",
    # Environment and process identity.
    "os.putenv": "the environment cannot be changed",
    "os.unsetenv": "the environment cannot be changed",
    "os.chdir": "the working directory cannot be changed",
}

#: Armed while the script runs. An audit hook fires for everything the process
#: does, and the interpreter's own work -- asyncio's event loop, a lazy stdlib
#: import, ``json``'s encoder -- would trip it. So the hook is installed once and
#: *armed* only around the script. The flag is thread-local, so the script has
#: no name for it and cannot disarm the guard.
_armed = threading.local()


def _install_audit_hook() -> None:
    messages = dict(_DENIED_EVENTS)
    _armed.depth = 0

    def hook(event: str, args: tuple[object, ...]) -> None:
        if getattr(_armed, "depth", 0) == 0:
            return
        debug_event = os.environ.get("VTX_CODEMODE_DEBUG")
        if debug_event and event == debug_event:
            # Walking frames here would recurse: traceback raises
            # sys._getframe, which is itself denied and would print again.
            print(f"[codemode] denied {event}: {args!r}", file=sys.stderr, flush=True)
            frame = sys._getframe(1)
            while frame is not None:
                print(
                    f"    {frame.f_code.co_filename}:{frame.f_lineno} {frame.f_code.co_name}",
                    file=sys.stderr,
                    flush=True,
                )
                frame = frame.f_back
        reason = messages.get(event)
        if reason is not None:
            raise SandboxViolation(f"{event}: {reason}")

    sys.addaudithook(hook)


@contextmanager
def script_authority() -> Iterator[None]:
    """Arm the deny hook for the duration of the script's own execution."""
    _armed.depth = getattr(_armed, "depth", 0) + 1
    try:
        yield
    finally:
        _armed.depth -= 1


def _install_import_guard() -> None:
    """Replace ``__import__`` with an allowlist check.

    ``sys.addaudithook`` cannot express "import anything outside this set", so
    the check lives here.
    """
    real_import = builtins.__import__

    def guarded_import(
        name: str,
        globals: object = None,
        locals: object = None,
        fromlist: object = (),
        level: int = 0,
    ) -> object:
        # The allowlist governs what *the script* may import, not what the
        # interpreter imports on its own account. The distinction is not
        # cosmetic: a module's own body imports its dependencies through the
        # same hook, so a blanket check would refuse the imports that every
        # allowlisted module needs to function.
        #
        # Passing interpreter-raised imports through is not a hole. Those
        # modules were resident before the guard went up, the script does not
        # choose which of them load, and every capability any of them can reach
        # is independently denied by the audit hook.
        caller = globals.get("__name__") if isinstance(globals, dict) else None
        if caller != _SCRIPT_MODULE:
            return real_import(name, globals, locals, fromlist, level)

        # A relative import resolves inside the script's own package, and the
        # script has no package on disk. Denying it also closes the
        # `__package__ = "asyncio"` trick, which would otherwise reach
        # asyncio.unix_events and from there subprocess.
        if level != 0 or not name:
            raise ImportError("relative imports are not available in the codemode sandbox")

        root = name.split(".", 1)[0]
        if name not in ALLOWED_IMPORTS and root not in ALLOWED_IMPORTS:
            raise ImportError(f"module {name!r} is not available in the codemode sandbox")
        return real_import(name, globals, locals, fromlist, level)

    builtins.__import__ = guarded_import


#: The ``__name__`` the script's namespace carries. Used to tell the script's own
#: imports apart from the interpreter's.
_SCRIPT_MODULE = "__codemode__"


def install_builtins(namespace: dict[str, Any]) -> None:
    """Replace ``__builtins__`` in ``namespace`` with the allowlist.

    Replacing the whole dict is what removes ``eval``, ``exec``, ``compile``,
    ``open``, ``getattr``, ``globals`` and ``breakpoint`` in one move, rather
    than by enumerating dangerous names and hoping the list is complete.
    """
    allowed = {
        name: getattr(builtins, name) for name in ALLOWED_BUILTINS if hasattr(builtins, name)
    }
    # ``__import__`` is not allowlisted, but IMPORT_NAME resolves it from
    # ``__builtins__`` -- so without it, ``import os`` fails with a bare
    # ``NameError: __import__ not found``, which tells the model nothing.
    # Pointing it at the guarded import turns that into a message naming the
    # module and saying why.
    allowed["__import__"] = builtins.__import__
    namespace["__builtins__"] = allowed


# --------------------------------------------------------------------------
# Frames
# --------------------------------------------------------------------------


def write_frame(payload: dict[str, Any]) -> None:
    """Write one frame as a single line to stdout.

    A single ``os.write`` matters: the script may ``print`` at any moment, and
    Python's buffered stdout is not under our control. One syscall per frame
    keeps frames atomic with respect to each other, which is what lets the
    parent parse whole lines.
    """
    data = (json.dumps(payload, ensure_ascii=False, default=str) + "\n").encode("utf-8")
    os.write(1, data)


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


def _prime_interpreter() -> None:
    """Resolve the event loop and default thread pool eagerly.

    Both are reached by ordinary script code -- awaiting a tool call touches
    ``asyncio.to_thread``, awaiting anything touches the event loop -- and both
    resolve by importing a module the guard correctly refuses. Priming them
    before the guard goes up means the script gets a working ``gather`` for
    free, without ``asyncio`` becoming an import it may perform.

    Best-effort: a failure here costs the script ``gather``, not its safety.
    """
    try:
        loop = asyncio.new_event_loop()
    except Exception:  # noqa: BLE001 - priming is advisory
        return
    try:
        asyncio.set_event_loop(loop)
    finally:
        loop.close()
        asyncio.set_event_loop(None)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(int).result()
    except Exception:  # noqa: BLE001 - same reasoning
        pass


class _CallResult:
    """One tool call's outcome, as the worker saw it."""

    __slots__ = ("kind", "message", "name", "status", "value")

    def __init__(
        self,
        name: str,
        status: str,
        value: Any = None,
        kind: str | None = None,
        message: str | None = None,
    ) -> None:
        self.name = name
        self.status = status
        self.value = value
        self.kind = kind
        self.message = message

    def as_frame(self) -> dict[str, Any]:
        frame: dict[str, Any] = {"tool": self.name, "status": self.status}
        if self.kind is not None:
            frame["kind"] = self.kind
        if self.message is not None:
            frame["message"] = self.message
        return frame


#: Matches pi-mono's fixed cap. An interpreter property, not a host knob.
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
                self._record(declared, "error", kind=ABORTED, message=remedy_for(ABORTED))
                raise ScriptAborted()
            if reply.get("type") != TOOL_RESULT or reply.get("id") != call_id:
                self._record(declared, "error", kind=SANDBOX)
                raise SandboxError("The host sent a mismatched tool reply.")
            if reply.get("ok"):
                value = reply.get("value")
                self._record(declared, "ok", value=value)
                return value
            error = reply.get("error") or {}
            kind = str(error.get("kind") or TOOL_FAILURE)
            message = str(error.get("message") or remedy_for(kind))
            self._record(declared, "error", kind=kind, message=message)
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
    ) -> None:
        with self._lock:
            self._calls.append(_CallResult(name, status, value, kind, message))

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


class _ScriptAsyncio:
    """The only part of ``asyncio`` the script gets.

    A namespace object rather than the module, because the module carries the
    event loop policy, the subprocess-backed default executor, and the whole
    ``AbstractEventLoop`` hierarchy. ``gather`` and ``sleep`` are what a script
    needs to express "these calls are independent"; ``wait_for`` is here
    because it is the natural way to bound a call that might hang.
    """

    _EXPORTS = ("gather", "sleep", "wait_for", "wait", "FIRST_COMPLETED", "ALL_COMPLETED")

    def __getattr__(self, name: str) -> Any:
        if name in self._EXPORTS:
            return getattr(asyncio, name)
        raise AttributeError(f"asyncio.{name} is not available in the codemode sandbox")

    def __dir__(self) -> list[str]:
        return [*super().__dir__(), *self._EXPORTS]

    def __repr__(self) -> str:
        return f"<asyncio (codemode: {', '.join(self._EXPORTS)})>"


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
        # Storing None deletes the key, matching pi-mono.
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
        "SandboxError",
    ):
        namespace[name] = globals()[name]


def _exec_script(code: str, namespace: dict[str, Any]) -> Any:
    """Run ``code`` as an async function body and return its result.

    The source is wrapped rather than compiled as a bare ``exec`` block so
    top-level ``return`` works, which is what the model expects when told it is
    writing a script body, and how pi-mono presents it.
    """
    body = indent(code, "    ")
    compiled = compile(
        f"async def __codemode_main__():\n{body or '    pass'}\n", "<codemode>", "exec"
    )
    exec(compiled, namespace)  # noqa: S102 - the sandbox is the boundary
    main = namespace.get("__codemode_main__")
    return None if main is None else asyncio.run(main())


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
    # A denial is not a script bug; report it as such so the model stops trying
    # rather than rewriting correct code.
    if isinstance(exc, (SandboxViolation, PermissionError, ImportError, OSError)):
        return {"kind": SANDBOX, "message": f"{type(exc).__name__}: {exc}", "stack": None}
    return {"kind": SCRIPT, "message": f"{type(exc).__name__}: {exc}", "stack": _script_stack(exc)}


def _script_stack(exc: BaseException) -> str:
    frames = traceback.extract_tb(exc.__traceback__)
    script_frames = [f for f in frames if f.filename == "<codemode>"]
    if not script_frames:
        return ""
    lines = traceback.format_list(script_frames)
    return f"Traceback (most recent call last):\n{''.join(lines)}{type(exc).__name__}: {exc}"


def run(request: dict[str, Any]) -> dict[str, Any]:
    """Execute one program and return the terminal ``result`` frame."""
    code = str(request.get("code") or "")
    declarations = request.get("tools") or []
    initial_store = request.get("store") or {}

    _prime_interpreter()
    bridge = _ToolBridge(sys.stdin)
    store = _Store(dict(initial_store))
    output: list[dict[str, Any]] = []

    def text(value: Any = "") -> None:
        """Append an output item. Non-strings are JSON-encoded."""
        rendered = (
            value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
        )
        output.append({"type": "text", "text": rendered})
        write_frame({"type": TEXT, "value": rendered})

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
        # `print` is deliberately NOT overridden with a host-side function. A
        # closure defined here would carry this module's __globals__, and the
        # script could read them straight to the interpreter. The genuine
        # builtin carries the builtins module instead, which has nothing worth
        # reaching. `text()` is the model-visible channel either way.
        "asyncio": _ScriptAsyncio(),
    }
    _install_exception_types(namespace)
    install_builtins(namespace)

    value: Any = None
    error: dict[str, Any] | None = None
    try:
        # Armed only here. Setup runs outside it, because the interpreter does
        # that work on its own account and denying it would break allowlisted
        # modules without making the script any more capable.
        with script_authority():
            value = _exec_script(code, namespace)
    except BaseException as exc:  # noqa: BLE001 - every failure becomes a diagnostic
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
    line = sys.stdin.readline()
    if not line:
        return 1
    try:
        request = json.loads(line)
    except json.JSONDecodeError:
        return 1

    _install_audit_hook()
    _install_import_guard()

    write_frame(run(request))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001 - last line of defense
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

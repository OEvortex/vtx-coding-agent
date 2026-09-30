"""CPython REPL runtime speaking newline-delimited JSON over stdio.

Entry point: ``python -m vtx.ai.agent.ipython_runtime`` (see
``vtx.ai.agent.ipython_runtime``). The wire format is documented next to the
Prime Agent original this module is ported from.

Cells execute with top-level await in one persistent ``__main__`` namespace on
a single asyncio event loop, so background tasks, imports, and variables
survive across cells and turns.

Ported from Prime Agent (MIT) — https://github.com/PrimeIntellect-ai/prime-agent
"""

from __future__ import annotations

import ast
import asyncio
import codecs
import contextlib
import contextvars
import ctypes
import importlib
import inspect
import io
import json
import linecache
import os
import platform
import re
import signal
import sys
import threading
import time
import traceback
import types
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .bash import _kill_live_handles

PROTOCOL_VERSION = 3

DEFAULT_SNAPSHOT_MAX_BYTES = 256 * 1024 * 1024
DEFAULT_SNAPSHOT_MAX_VARIABLE_BYTES = 16 * 1024 * 1024

# Plain ASCII, never a pickle start: _restore_state sniffs it to tell v2 framed
# payloads from legacy (single dill-pickled dict) ones.
_SNAPSHOT_MAGIC = b"PRIME-AGENT-KERNEL-SNAPSHOT-V2\n"

# Stream writes must fit one protocol frame: the host buffers whole lines
# before its per-execution truncation, and raw fd writes already arrive as
# 64 KiB pump chunks.
_STREAM_FRAME_TEXT_CAP = 64 * 1024
# The host truncates results at a smaller per-execution maxChars, so this only
# bounds a pathological repr or exception text in transit.
_RESULT_TEXT_CAP = 1_048_576
# Prime parity for file reads: 2000 lines AND 50KB, whichever hits first.
_READ_FILE_MAX_BYTES = 50 * 1024
_RESULT_TRUNCATION_MARKER = f"\n[... result truncated at {_RESULT_TEXT_CAP} characters ...]"
# Oversized display and host_request payloads fail the cell instead of wedging host memory.
_PAYLOAD_CAP = 16 * 1024 * 1024

# Names the kernel bootstrap re-creates on every start; never snapshotted.
_ALWAYS_SKIP = {
    "rlm",
    "mcp",
    "bash",
    "asyncio",
    "In",
    "Out",
    "get_ipython",
    "exit",
    "quit",
    "open",
    # VTX pre-bound helpers (see _init_builtin_helpers).
    "run_bash",
    "read_file",
    "write_file",
    "edit_file",
    "run_code",
    "rerun",
    "find_tools",
    "describe_tool",
    "web_search",
    "goal_get",
    "goal_update",
    "goal_set_tasks",
    "call_tool",
    "context",
    "emit",
    "host_request",
}
# IPython-injected names that may appear in a snapshot payload; never restored.
_RESTORE_SKIP = {"In", "Out", "get_ipython"}

_MAX_OUT_ENTRIES = 1000  # Cap In/Out history to prevent unbounded memory growth

_protocol_fd: int = -1
_write_lock = threading.Lock()
_loop: asyncio.AbstractEventLoop | None = None
_serve_task: asyncio.Task[Any] | None = None
_namespace: dict[str, Any] = {}


class _CellExecution:
    def __init__(self) -> None:
        import asyncio

        self.finished = asyncio.Event()
        self.owner: asyncio.Task[Any] | None = None


# Asyncio tasks copy cell context at creation, so detached tasks retain their
# output attribution and completion barrier. Threads start with a fresh context.
_current_cell: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_current_cell", default=None
)
_current_cell_execution: contextvars.ContextVar[_CellExecution | None] = contextvars.ContextVar(
    "_current_cell_execution", default=None
)
_active: dict[str, Any] = {"task": None, "rid": None, "interrupted": False}
_cell_counter = 0
_pending_host: dict[str, asyncio.Future[dict[str, Any]]] = {}
# Set on the loop thread once stdin hits EOF or a shutdown request arrives; no
# host reply can arrive after that, so waiting (and future) host_request calls fail.
_host_closed = False

# Interrupt bookkeeping shared between the reader thread and the loop thread.
_interrupt_lock = threading.Lock()
_inflight: set[str] = set()
_pending_interrupts: dict[str, Any] = {"ids": set(), "any": False}
_sigint_target: str | None = None
_finishing_rid: str | None = None
_handoff_interrupted = False

# Sync (blocking) tool bridge used by the VTX pre-bound helpers. These wait on
# a threading.Event rather than an asyncio.Future so a helper can block inside
# a cell without needing the loop to deliver its reply.
_tool_call_responses: dict[str, Any] = {}
_tool_call_events: dict[str, threading.Event] = {}

# IPython-style execution history: In[n] has input code string, Out[n] has returned repr/value.
In: list[str] = [""]
Out: dict[int, Any] = {}
_cell_number = 0


# =================================================================================================
# Protocol I/O
# =================================================================================================


def _send(event: dict[str, Any]) -> None:
    """Write one protocol frame; the locked single write keeps frames atomic."""
    data = (json.dumps(event, separators=(",", ":")) + "\n").encode()
    with _write_lock:
        view = memoryview(data)
        try:
            while view:
                view = view[os.write(_protocol_fd, view) :]
        except OSError:
            pass


def _check_payload(event: str, data: dict[str, Any]) -> None:
    """Fail the calling cell when a `data` payload would not fit one protocol frame.

    Strict-dumps validation: default allow_nan=True would let NaN/Infinity
    serialize as non-JSON text and tear the host's protocol framing (a
    non-serializable value raises TypeError here before any bytes are
    written, so NaN is the only corruption vector). The encoded length
    enforces the frame cap; _send re-serializes.
    """
    if len(json.dumps(data, allow_nan=False)) > _PAYLOAD_CAP:
        raise ValueError(f"{event} payload exceeds the {_PAYLOAD_CAP}-character frame cap")


def emit(data: dict[str, Any]) -> None:
    """Ship one display event carrying a dict of MIME type -> JSON payload.

    Thread-safe; the event is tagged with the cell running at call time.
    """
    if not isinstance(data, dict) or not data or not all(isinstance(k, str) for k in data):
        raise TypeError("emit() requires a non-empty dict keyed by MIME type strings")
    _check_payload("display", data)
    _send({"event": "display", "id": _current_cell.get(), "data": data})


def is_active() -> bool:
    """True when this process serves the repl protocol (not merely imported)."""
    return _protocol_fd >= 0


def current_cell_completion_context() -> tuple[asyncio.Event, asyncio.Task[Any] | None] | None:
    """Return the calling cell's completion barrier and owning execution task."""
    execution = _current_cell_execution.get()
    if execution is None:
        return None
    return execution.finished, execution.owner


def active_cell_task() -> asyncio.Task[Any] | None:
    """Body task of the cell executing right now, or None between cells (global
    state, not the cell contextvar — detached tasks keep stale context copies)."""
    import asyncio

    with _interrupt_lock:
        task = _active["task"]
    return task if isinstance(task, asyncio.Task) and not task.done() else None


async def host_request(data: dict[str, Any]) -> dict[str, Any]:
    """Send one typed request to the host and await its raw reply dict."""
    if _loop is None:
        raise RuntimeError("repl runtime is not serving")
    if _host_closed:
        raise RuntimeError("host connection closed; host_request cannot be answered")
    _check_payload("host_request", data)
    rid = uuid.uuid4().hex
    future: asyncio.Future[dict[str, Any]] = _loop.create_future()
    _pending_host[rid] = future
    try:
        _send({"event": "host_request", "id": rid, "data": data})
        return await future
    finally:
        _pending_host.pop(rid, None)


def _fail_pending_host_requests() -> None:
    """Loop-thread half of teardown: no host reply can arrive anymore, so every
    awaiting cell must unblock or the queued shutdown would never be served."""
    global _host_closed
    _host_closed = True
    for future in _pending_host.values():
        if not future.done():
            future.set_exception(
                RuntimeError("host connection closed; host_request cannot be answered")
            )


def _resolve_host_reply(rid: str, data: Any) -> None:
    """Reader-thread half of the host bridge; late/unknown replies are dropped."""
    # Blocking call_tool() waiters are woken directly: they run inside a cell
    # on the loop thread, so a loop hop here would deadlock.
    event = _tool_call_events.pop(rid, None)
    if event is not None:
        _tool_call_responses[rid] = data
        event.set()
        return
    loop = _loop
    if loop is None:
        return

    def deliver() -> None:
        future = _pending_host.get(rid)
        if future is not None and future.done() is False:
            future.set_result(data)

    loop.call_soon_threadsafe(deliver)


def call_tool(name: str, **kwargs: Any) -> Any:
    """Call a main-process tool from the REPL (blocking, sync).

    Raises on tool error or timeout. Prefer an async ``host_request`` from new
    code; this exists for the pre-bound sync helpers (``web_search``,
    ``goal_get``, ...).
    """
    if not is_active():
        raise RuntimeError("call_tool requires a running VTX REPL kernel")
    rid = uuid.uuid4().hex
    event = threading.Event()
    _tool_call_events[rid] = event
    _send(
        {
            "event": "host_request",
            "id": rid,
            "data": {"type": "tool.call", "name": name, "args": kwargs},
        }
    )
    if not event.wait(timeout=300):
        _tool_call_events.pop(rid, None)
        raise TimeoutError(f"Tool call {name} timed out after 300s")
    result = _tool_call_responses.pop(rid, None)
    if isinstance(result, dict) and "status" in result:
        # Typed host reply: {"status": "ok", "result": ...} / {"status": "error", ...}
        if result.get("status") == "ok":
            return result.get("result")
        raise RuntimeError(str(result.get("error") or f"tool {name} failed"))
    if isinstance(result, dict) and "error" in result:
        raise RuntimeError(result["error"])
    return result


#: Bridge name that returns the whole tool surface. Routed by
#: ``IpythonTool`` to the ``tool.catalog`` host request rather than to a real
#: tool, so it never appears in the model's tool list.
_TOOL_CATALOG_BRIDGE = "__vtx_tool_catalog__"

#: Bridge names for the session-backed continual harness. The kernel is a
#: separate process with no access to the session tree, so reads and writes go
#: through the host, which owns the branch.
_HARNESS_READ_BRIDGE = "__vtx_harness_read__"
_HARNESS_WRITE_BRIDGE = "__vtx_harness_write__"

#: Cached answer to "is the host serving a session?". Probed once, because every
#: harness access would otherwise pay a round trip to learn it is unavailable.
_harness_bridge_available: bool | None = None


def branch_backed_harness_state() -> Any | None:
    """A :class:`HarnessState` persisted in the host's session tree, or ``None``.

    Returns ``None`` when the host has no session to write to (``--no-session``)
    or the bridge is unreachable, so the caller falls back to the file-backed
    store and keeps its existing "no persistent local harness store" error. That
    matters: silently accepting writes that go nowhere is worse than refusing
    them.
    """
    global _harness_bridge_available

    from .harness import HarnessState, get_harness_state

    if not is_active():
        return get_harness_state()
    if _harness_bridge_available is False:
        return get_harness_state()

    def read() -> tuple[dict[str, Any], list[dict[str, Any]]]:
        reply = call_tool(_HARNESS_READ_BRIDGE)
        if not isinstance(reply, dict) or not reply.get("available"):
            raise RuntimeError("host session store is unavailable")
        entries = reply.get("entries")
        refinements = reply.get("refinements")
        return (
            entries if isinstance(entries, dict) else {},
            refinements if isinstance(refinements, list) else [],
        )

    def write(
        added: dict[str, dict[str, Any]], removed: list[str], refinements: list[dict[str, Any]]
    ) -> None:
        call_tool(_HARNESS_WRITE_BRIDGE, set=added, delete=removed, refinements=refinements)

    try:
        read()
    except Exception:
        _harness_bridge_available = False
        return get_harness_state()
    _harness_bridge_available = True
    return HarnessState(in_memory=True, scope="local", branch_reader=read, branch_writer=write)


class _ToolDiscovery:
    """Cached, lazily populated view of the callable tool surface.

    The tool set only changes when the session reloads, and a search is
    synchronous (a cell cannot await a discovery round trip inside
    ``call_tool``-style code without an event loop), so the catalog is fetched
    once over the async bridge and kept. ``_clear_tool_discovery()`` drops it
    when the surface can have changed.
    """

    _tools: list[dict[str, Any]] | None = None
    _by_name: dict[str, dict[str, Any]] | None = None

    @classmethod
    def catalog(cls) -> list[dict[str, Any]]:
        if cls._tools is None:
            # ``call_tool`` blocks on a thread, so this is safe from sync cell
            # code; the reply is the cached surface, not a per-query search.
            # A bridge failure is not fatal: discovery degrades to "no results"
            # rather than taking down the cell that asked.
            try:
                reply = call_tool(_TOOL_CATALOG_BRIDGE)
            except Exception:
                cls._tools = []
                cls._by_name = {}
                return cls._tools
            tools = reply.get("tools") if isinstance(reply, dict) else None
            cls._tools = tools if isinstance(tools, list) else []
            cls._by_name = {
                str(tool.get("name")): tool
                for tool in cls._tools
                if isinstance(tool, dict) and tool.get("name")
            }
        return cls._tools

    @classmethod
    def find(cls, query: str, limit: int) -> list[dict[str, Any]]:
        """Rank the catalog locally with the same BM25 the host uses.

        Ranking in the kernel rather than shipping the query to the host keeps
        ``find_tools`` usable from synchronous cell code and makes repeat
        searches free.
        """
        from .toolsearch import rank, tool_document

        catalog = cls.catalog()
        if not catalog:
            return []
        documents = [
            tool_document(
                _Doc(
                    name=str(tool.get("name", "")),
                    description=str(tool.get("description", "") or ""),
                    parameters=tool.get("parameters") or {},
                )
            )
            for tool in catalog
            if isinstance(tool, dict)
        ]
        by_name = {document.name: document for document in documents}
        matches = rank(query, documents, limit)
        return [
            {
                "name": match.name,
                "description": by_name[match.name].description if match.name in by_name else "",
            }
            for match in matches
        ]

    @classmethod
    def describe(cls, name: str) -> dict[str, Any] | None:
        if cls._by_name is None:
            cls.catalog()
        return (cls._by_name or {}).get(name)


class _Doc:
    """Minimal tool stand-in so :func:`tool_document` can read a catalog entry."""

    __slots__ = ("description", "name", "parameters")

    def __init__(self, name: str, description: str, parameters: Any) -> None:
        self.name = name
        self.description = description
        self.parameters = parameters


def _clear_tool_discovery() -> None:
    """Drop the cached catalog; the next search refetches it."""
    _ToolDiscovery._tools = None
    _ToolDiscovery._by_name = None


def _search_tools(query: str, limit: int = 8) -> list[dict[str, Any]]:
    return _ToolDiscovery.find(query, limit)


def _describe_tool(name: str) -> dict[str, Any] | None:
    return _ToolDiscovery.describe(name)


# =================================================================================================
# Stream capture (tagged Python-level writes + raw fd pipes)
# =================================================================================================


class _Pump:
    """Reads one captured-output pipe and ships its bytes as stream events."""

    def __init__(self, read_fd: int, write_fd: int, stream: str) -> None:
        self._read_fd = read_fd
        # Private write end: a cell closing/reclaiming fd 1/2 cannot hijack drain tokens.
        self._token_fd = os.dup(write_fd)
        self._stream = stream
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._lock = threading.Lock()
        self._watch: tuple[bytes, threading.Event] | None = None
        self._buf = b""
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def drain(self) -> None:
        """Block until every byte written to the fd so far has been shipped."""
        token = b"\xff<drain:" + uuid.uuid4().hex.encode() + b">\xff"
        seen = threading.Event()
        with self._lock:
            self._watch = (token, seen)
        try:
            os.write(self._token_fd, token)
            # Backstop only: a dead pump (read end closed under it) can never set seen.
            while not seen.wait(0.1):
                if not self._thread.is_alive():
                    return
        except OSError:
            return
        finally:
            with self._lock:
                self._watch = None

    def _run(self) -> None:
        while True:
            try:
                chunk = os.read(self._read_fd, 65536)
            except OSError:
                break
            if not chunk:
                break
            self._feed(chunk)

    def _feed(self, chunk: bytes) -> None:
        data = self._buf + chunk
        self._buf = b""
        with self._lock:
            watch = self._watch
        if watch is None:
            self._emit(data)
            return
        token, seen = watch
        while True:
            i = data.find(token)
            if i == -1:
                break
            self._emit(data[:i])
            self._finish_decode()
            seen.set()
            data = data[i + len(token) :]
        # Hold back a tail that could be the start of a token split across reads.
        hold = 0
        for k in range(min(len(data), len(token) - 1), 0, -1):
            if data.endswith(token[:k]):
                hold = k
                break
        if hold:
            self._buf = data[len(data) - hold :]
            data = data[: len(data) - hold]
        self._emit(data)

    def _emit(self, data: bytes) -> None:
        if not data:
            return
        text = self._decoder.decode(data)
        if text:
            # Raw fd bytes have no provable owner (os.write, C extensions,
            # subprocesses, threads from earlier cells): never credit a cell.
            _send({"event": self._stream, "id": None, "text": text})

    def _finish_decode(self) -> None:
        text = self._decoder.decode(b"", final=True)
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        if text:
            _send({"event": self._stream, "id": None, "text": text})


class _TaggedBuffer(io.RawIOBase):
    """Binary proxy for _TaggedWriter.buffer: bytes go to the raw fd channel.

    Byte ownership cannot be proven at this layer, so buffer writes ride the
    captured pipe and surface as id:null stream events (drained before done
    like any other raw fd write).
    """

    def __init__(self, fallback_fd: int) -> None:
        self._fallback_fd = fallback_fd

    def write(self, data: Any) -> int:
        # memoryview (unlike bytes()) rejects int, matching a real buffer's TypeError.
        view = memoryview(data).cast("B")
        total = len(view)
        # Pipe writes can be short for payloads above the pipe capacity.
        while view:
            view = view[os.write(self._fallback_fd, view) :]
        return total

    def flush(self) -> None:
        pass

    def fileno(self) -> int:
        return self._fallback_fd

    def writable(self) -> bool:
        return True


class _TaggedWriter(io.TextIOBase):
    """sys.stdout/sys.stderr replacement tagging writes with the writer's cell id.

    Python-level writes carry write-time provenance from the _current_cell
    contextvar (asyncio tasks inherit the spawning cell's id; user threads see
    None) and ship straight to the protocol, bypassing the fd pipe. fileno()
    and .buffer expose the captured pipe so subprocesses, C-level writers, and
    sys.stdout.buffer.write() keep working through the raw channel
    (null-attributed).
    """

    def __init__(self, stream: str, fallback_fd: int) -> None:
        self._stream = stream
        self._fallback_fd = fallback_fd
        # Keeps one write()'s frames contiguous under concurrent writers.
        self._frame_lock = threading.Lock()
        self._buffer = _TaggedBuffer(fallback_fd)

    def write(self, text: str) -> int:
        if not isinstance(text, str):
            raise TypeError(f"write() argument must be str, not {type(text).__name__}")
        if text:
            cell_id = _current_cell.get()
            with self._frame_lock:
                for start in range(0, len(text), _STREAM_FRAME_TEXT_CAP):
                    _send(
                        {
                            "event": self._stream,
                            "id": cell_id,
                            "text": text[start : start + _STREAM_FRAME_TEXT_CAP],
                        }
                    )
        return len(text)

    def flush(self) -> None:
        pass

    def fileno(self) -> int:
        return self._fallback_fd

    def writable(self) -> bool:
        return True

    @property
    def buffer(self) -> _TaggedBuffer:
        return self._buffer

    @property
    def encoding(self) -> str:
        return "utf-8"

    @property
    def errors(self) -> str:
        return "replace"


# =================================================================================================
# Interrupt machinery
# =================================================================================================


def _consume_task_exception(task: asyncio.Task[Any]) -> None:
    """Retrieve a killed task's exception so no never-retrieved noise is logged."""
    if not task.cancelled():
        task.exception()


def _sigint_handler(signum: int, frame: types.FrameType | None) -> None:
    # asyncio loads by the time any task can be active (main() imports it), so
    # this is a cached sys.modules hit even inside the signal handler.
    import asyncio

    global _handoff_interrupted
    task = _active["task"]
    # No lock (the main thread may hold it): the rid equality revalidates the
    # target so a SIGINT delayed past its request's finish cannot hit a later cell.
    if task is None or task.done() or _active["rid"] != _sigint_target:
        if _sigint_target is not None and _sigint_target == _active["rid"]:
            # Handoff: the task is done but _run_guarded's finally has not run
            # yet, so the main thread may be inside loop internals where raising
            # would kill the serve loop. Record it; the finishing phase consumes it.
            _handoff_interrupted = True
            return
        # Post-run repr/drain is synchronous main-thread work: raise into it.
        # The equality revalidation drops a SIGINT delayed past the done send.
        if _sigint_target is not None and _sigint_target == _finishing_rid:
            raise KeyboardInterrupt
        return
    _active["interrupted"] = True
    # Handler runs in the main (loop) thread: current_task is whose step the signal interrupted.
    running = asyncio.current_task(_loop) if _loop is not None else None
    if running is task:
        # The active request's own step (sync bytecode or an EINTR-woken syscall): raise into it.
        raise KeyboardInterrupt
    # Loop idle in select() or another task mid-step: cancel the active task (same thread, safe).
    task.cancel()
    if running is not None and running is not _serve_task:
        # A background task blocked in sync code occupies the only thread and would keep the
        # cancel from ever running: raise into it to unwind its step; it dies with the KI.
        running.add_done_callback(_consume_task_exception)
        raise KeyboardInterrupt


def _request_interrupt(target: str | None) -> None:
    """Deliver an interrupt now, or park it for the request it targets.

    Runs on the reader thread. Without a target id the interrupt applies to
    the running request, else to the next queued one; with a target id it
    applies to that request only. A request finishing its post-run repr/drain
    is still interrupted (never parked: parking would hit the NEXT request).
    Interrupts for finished or unknown requests are dropped.
    """
    global _sigint_target
    with _interrupt_lock:
        task = _active["task"]
        rid = _active["rid"]
        if rid is not None and (target is None or target == rid):
            # Active, or in the done-task handoff before _run_guarded's finally:
            # either way the rid still owns the interrupt (parking here would
            # leak it onto the next request); the handler decides delivery.
            _sigint_target = rid
        elif _finishing_rid is not None and (target is None or target == _finishing_rid):
            _sigint_target = _finishing_rid
        elif target is not None:
            if target in _inflight:
                _pending_interrupts["ids"].add(target)
            return
        elif _inflight:
            _pending_interrupts["any"] = True
            return
        else:
            return
    # SIGINT must land on the main thread, where cells execute. Windows has no
    # signal.pthread_kill: fall back to cancelling the active task on the loop
    # (sync-blocked cells and the finishing repr/drain cannot be broken there;
    # best-effort parity).
    if hasattr(signal, "pthread_kill"):
        main_ident = threading.main_thread().ident
        if main_ident is None:  # only possible before the thread is started
            return
        signal.pthread_kill(main_ident, signal.SIGINT)
        if _loop is not None:
            # Wake the selector so a cancel scheduled by the handler runs promptly.
            _loop.call_soon_threadsafe(lambda: None)
        return
    if _loop is not None:

        def cancel_active() -> None:
            current = _active["task"]
            if current is task and current is not None and not current.done():
                _active["interrupted"] = True
                current.cancel()

        _loop.call_soon_threadsafe(cancel_active)


def _consume_pending_interrupt(rid: str) -> bool:
    """Check-and-clear any interrupt parked for this request."""
    pending = _pending_interrupts["any"] or rid in _pending_interrupts["ids"]
    _pending_interrupts["any"] = False
    _pending_interrupts["ids"].discard(rid)
    return pending


def _consume_handoff_interrupt() -> bool:
    """Check-and-clear an interrupt that landed in the done-task handoff."""
    global _handoff_interrupted
    with _interrupt_lock:
        pending = _handoff_interrupted
        _handoff_interrupted = False
        return pending


def _finish_locked(rid: str) -> None:
    """Drop a finished request; a parked untargeted interrupt survives while
    others are inflight."""
    global _finishing_rid, _handoff_interrupted, _sigint_target
    if _finishing_rid == rid:
        # An unconsumed handoff interrupt dies with its request (state requests
        # have no cancellable post-run work); it must never hit the next request.
        _finishing_rid = None
        _handoff_interrupted = False
    if _sigint_target == rid:
        # The target dies with its request: a later request reusing this id must
        # not match a stale target when a delayed/external SIGINT arrives.
        _sigint_target = None
    _inflight.discard(rid)
    _pending_interrupts["ids"].discard(rid)
    if not _inflight:
        _pending_interrupts["any"] = False


def _finish_request(rid: str) -> None:
    with _interrupt_lock:
        _finish_locked(rid)


_RUNTIME_FILE = __file__


def _cell_stack(stack: traceback.StackSummary) -> traceback.StackSummary | None:
    """Frames from the first cell frame on, minus runtime-internal frames.

    Returns None when no cell frame exists (e.g. a compile-time SyntaxError).
    """
    start = next((i for i, f in enumerate(stack) if f.filename.startswith("<cell-")), None)
    if start is None:
        return None
    return traceback.StackSummary.from_list(
        [f for f in stack[start:] if f.filename != _RUNTIME_FILE]
    )


def _safe_str(exc: BaseException) -> str:
    try:
        return str(exc)
    except BaseException:
        return "<exception str() failed>"


def _cap_text(text: str) -> str:
    if len(text) > _RESULT_TEXT_CAP:
        return text[:_RESULT_TEXT_CAP] + _RESULT_TRUNCATION_MARKER
    return text


def _error_event(cell_id: str, exc: BaseException) -> dict[str, Any]:
    # A host-bridge failure is a categorized answer, not a defect in the cell.
    # Printing a Python traceback for it would bury the category under frames
    # from the host's own call path and read as "my code is broken".
    from vtx.ai.agent.rlm.diagnostics import BridgeError

    if isinstance(exc, BridgeError):
        return {
            "event": "error",
            "id": cell_id,
            "ename": f"BridgeError[{exc.kind}]",
            "evalue": exc.message,
            "traceback": exc.render().splitlines(),
            "bridgeKind": exc.kind,
            "retryable": exc.retryable,
        }

    # No cell frame (e.g. SyntaxError): exception-only keeps filename, source, and caret.
    te = traceback.TracebackException.from_exception(exc)
    stack = _cell_stack(te.stack)
    if stack is None:
        lines = traceback.format_exception_only(type(exc), exc)
    else:
        te.stack = stack
        lines = list(te.format())
    return {
        "event": "error",
        "id": cell_id,
        "ename": type(exc).__name__,
        "evalue": _cap_text(_safe_str(exc)),
        "traceback": [_cap_text(line) for line in lines],
    }


def _interrupt_event(cell_id: str, exc: BaseException) -> dict[str, Any]:
    """Report a cancelled await-suspended cell as a KeyboardInterrupt."""
    stack = _cell_stack(traceback.extract_tb(exc.__traceback__))
    lines = []
    if stack:
        lines = ["Traceback (most recent call last):\n"]
        lines.extend(stack.format())
    lines.append("KeyboardInterrupt\n")
    return {
        "event": "error",
        "id": cell_id,
        "ename": "KeyboardInterrupt",
        "evalue": "",
        "traceback": lines,
    }


def transform_cell_code(code: str) -> str:
    """Transform IPython cell magics (%%bash) and shell escapes (!cmd) into Python code."""
    trimmed = code.rstrip()
    if not trimmed:
        return code

    # Check for %%bash cell magic
    m_bash = re.match(r"^(?:[ \t]*\r?\n)*[ \t]*%%bash\b[^\r\n]*(?:\r?\n|$)", trimmed)
    if m_bash:
        bash_body = trimmed[m_bash.end() :]
        escaped = bash_body.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
        return f'run_bash("""{escaped}""")'

    # Transform lines starting with ! into run_bash(...)
    lines = code.splitlines(keepends=True)
    transformed_lines: list[str] = []
    has_transforms = False
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("!"):
            indent = line[: len(line) - len(stripped)]
            cmd = stripped[1:].strip()
            escaped = cmd.replace("\\", "\\\\").replace('"', '\\"')
            transformed_lines.append(f'{indent}run_bash("{escaped}")\n')
            has_transforms = True
        else:
            transformed_lines.append(line)

    return "".join(transformed_lines) if has_transforms else code


def _compile_cell(code: str, filename: str) -> tuple[list[types.CodeType], bool]:
    """Compile a cell; a trailing expression compiles separately in eval mode."""
    linecache.cache[filename] = (len(code), None, code.splitlines(keepends=True), filename)
    tree = ast.parse(code, filename)
    trailing: ast.Expression | None = None
    if tree.body and isinstance(last := tree.body[-1], ast.Expr):
        tree.body.pop()
        trailing = ast.Expression(last.value)
    flags = ast.PyCF_ALLOW_TOP_LEVEL_AWAIT
    codes: list[types.CodeType] = []
    if tree.body:
        codes.append(compile(tree, filename, "exec", flags=flags, dont_inherit=True))
    if trailing is not None:
        codes.append(compile(trailing, filename, "eval", flags=flags, dont_inherit=True))
    return codes, trailing is not None


async def _run_codes(codes: list[types.CodeType], ns: dict[str, Any]) -> Any:
    value: Any = None
    for code_obj in codes:
        value = eval(code_obj, ns)
        if code_obj.co_flags & inspect.CO_COROUTINE:
            value = await value
    return value


async def _run_guarded(
    task: asyncio.Task[Any], rid: str
) -> tuple[str, Any, dict[str, Any] | None]:
    """Await a request task; returns (status, value, error event or None)."""
    import asyncio

    with _interrupt_lock:
        _active["interrupted"] = False
        _active["rid"] = rid
        _active["task"] = task
        if _consume_pending_interrupt(rid):
            # Interrupt parked before activation: cancel before the first step.
            _active["interrupted"] = True
            task.cancel()
    try:
        value = await task
        return "ok", value, None
    except asyncio.CancelledError as exc:
        if _active["interrupted"]:
            return "error", None, _interrupt_event(rid, exc)
        return "error", None, _error_event(rid, exc)
    except BaseException as exc:
        return "error", None, _error_event(rid, exc)
    finally:
        with _interrupt_lock:
            global _finishing_rid
            # The rid stays inflight and interrupt-targetable through the
            # post-run repr/drain; the handler closes the window via _finish_request.
            # Set before clearing _active: the lock-free handler must always see
            # the rid in one of the two slots, never a torn in-between state.
            _finishing_rid = rid
            _active["task"] = None
            _active["rid"] = None


# =================================================================================================
# VTX execution-history helpers (In / Out / _i / _ii / _iii)
# =================================================================================================


def _record_history(code: str) -> int:
    global _cell_number
    with _state_lock:
        _cell_number += 1
        cell_num = _cell_number
        In.append(code)
        if len(In) > _MAX_OUT_ENTRIES + 1:
            del In[1 : len(In) - _MAX_OUT_ENTRIES]
        _namespace["In"] = In
        _namespace["_ih"] = In
        _namespace["_i"] = code
        if len(In) > 2:
            _namespace["_ii"] = In[-2]
        if len(In) > 3:
            _namespace["_iii"] = In[-3]
    return cell_num


def _record_result(value: Any, cell_num: int) -> None:
    with _state_lock:
        if "_" in _namespace:
            if "__" in _namespace:
                _namespace["___"] = _namespace["__"]
            _namespace["__"] = _namespace["_"]
        _namespace["_"] = value
        Out[cell_num] = value
        # Evict oldest Out entries if over limit to prevent memory leak
        if len(Out) > _MAX_OUT_ENTRIES:
            overflow = len(Out) - _MAX_OUT_ENTRIES
            for old_key in sorted(Out.keys())[:overflow]:
                del Out[old_key]
        _namespace["Out"] = Out
        _namespace["_oh"] = Out


_state_lock = threading.Lock()


async def _handle_execute(req: dict[str, Any], ns: dict[str, Any]) -> None:
    global _cell_counter
    cell_id = req["id"]
    _cell_counter += 1
    filename = f"<cell-{_cell_counter}>"
    execution = _CellExecution()
    cell_token = _current_cell.set(cell_id)
    execution_token = _current_cell_execution.set(execution)
    ctx_dict = req.get("context")
    if ctx_dict is not None or "context" not in ns:
        _update_context_in_namespace(ctx_dict)
    # The tool surface can change on reload or a mode switch, so the cached
    # discovery catalog is dropped whenever a context is re-pushed.
    if ctx_dict is not None:
        _clear_tool_discovery()
    code = transform_cell_code(req.get("code", ""))
    cell_num = _record_history(code)
    try:
        codes, has_trailing = _compile_cell(code, filename)
        assert _loop is not None
        task = _loop.create_task(_run_codes(codes, ns))
        execution.owner = task
        status, value, error = await _run_guarded(task, cell_id)
        result_text: str | None = None
        try:
            if _consume_handoff_interrupt() and status == "ok":
                # SIGINT landed between the task's completion and the finishing
                # phase: it targeted this request, so cancel its remaining work.
                status, error = "error", _error_event(cell_id, KeyboardInterrupt())
            if status == "ok" and has_trailing and value is not None:
                try:
                    _record_result(value, cell_num)
                    result_text = repr(value)
                except BaseException as exc:
                    status, error = "error", _error_event(cell_id, exc)
            if result_text is not None:
                result_text = _cap_text(result_text)
            _drain_output()
        finally:
            # Close the interrupt window before the protocol sends so a
            # handler-raised KeyboardInterrupt can never tear a frame mid-_send.
            _finish_request(cell_id)
        shadowed = _restore_shadowed_helpers(ns)
        if shadowed:
            # Reported in the cell that did it, which is where the model is
            # looking, and only when the cell got far enough to run.
            names = ", ".join(f"`{name}`" for name in shadowed)
            _send(
                {
                    "event": "stdout",
                    "id": cell_id,
                    "text": (
                        f"\n[restored {names}: these names are bound to the REPL helpers "
                        "and cannot be reassigned. Pick a different name, or call the "
                        "helper you meant to shadow through `harness`/`rlm` or "
                        "`import` if it is not one of these.]"
                    ),
                }
            )
        if result_text is not None:
            _send({"event": "result", "id": cell_id, "text": result_text})
        if error is not None:
            _send(error)
        _send({"event": "done", "id": cell_id, "status": status})
    finally:
        execution.owner = None
        execution.finished.set()
        _current_cell_execution.reset(execution_token)
        _current_cell.reset(cell_token)


def _drain_output() -> None:
    # Per-stream, and ValueError too: a cell may close sys.stdout/sys.stderr, and
    # flushing a closed file raises ValueError, or rebind them to a flush-less
    # object (AttributeError); neither may kill the serve loop nor skip flushing
    # the other stream.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except (OSError, ValueError, AttributeError):
            pass
    _pump_out.drain()
    _pump_err.drain()


# =================================================================================================
# Snapshot / restore / list_names
# =================================================================================================


class _SnapshotSizeLimitExceeded(Exception):
    pass


class _CappedWriter:
    def __init__(self, sink: Any, limit: int) -> None:
        self._sink = sink
        self._limit = limit
        self.written = 0

    def write(self, chunk: Any) -> int:
        size = len(chunk)
        if self.written + size > self._limit:
            raise _SnapshotSizeLimitExceeded()
        self._sink.write(chunk)
        self.written += size
        return size


def _read_snapshot_records(fh: Any) -> dict[str, bytes]:
    """Framing damage is a corrupt snapshot: a restore error, never a partial namespace.
    Length fields are bounds-checked before their reads, so a corrupt header
    cannot force a huge allocation."""
    fh.seek(0, os.SEEK_END)
    size = fh.tell()
    fh.seek(len(_SNAPSHOT_MAGIC))
    records: dict[str, bytes] = {}
    while fh.tell() < size:
        header = fh.read(4)
        if len(header) < 4:
            raise ValueError("truncated snapshot record")
        name_len = int.from_bytes(header, "little")
        if fh.tell() + name_len + 8 > size:
            raise ValueError("truncated snapshot record")
        name = fh.read(name_len)
        raw_len = fh.read(8)
        blob_len = int.from_bytes(raw_len, "little")
        if len(raw_len) < 8 or fh.tell() + blob_len > size:
            raise ValueError("truncated snapshot record")
        blob = fh.read(blob_len)
        if len(blob) < blob_len:
            raise ValueError("truncated snapshot record")
        records[name.decode("utf-8")] = blob
    return records


def _snapshot_state(
    ns: dict[str, Any],
    path: str,
    manifest_path: str,
    max_bytes: int,
    max_variable_bytes: int,
    prune_oversized: bool,
    committed: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    import datetime
    import tempfile

    try:
        import dill  # ty: ignore[unresolved-import]  # optional dep; absence degrades to an error reply
    except Exception as err:
        return {"error": f"dill unavailable: {err}"}
    dill.settings["recurse"] = True

    saved: list[str] = []
    skipped: list[dict[str, str]] = []
    oversized: list[str] = []
    missing = object()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    temps: list[str] = []

    def stage_temp(target: str, mode: str):
        # Unique same-directory temps: a fixed '.tmp' name could alias the other
        # final path (clobbering it) or collide with a concurrent snapshot.
        fd, name = tempfile.mkstemp(
            dir=os.path.dirname(target) or ".",
            prefix=os.path.basename(target) + ".",
            suffix=".tmp",
        )
        temps.append(name)
        try:
            return os.fdopen(fd, mode), name
        except BaseException:
            os.close(fd)  # fdopen never took ownership: the raw fd would leak
            raise

    def discard_temps() -> None:
        for stale in temps:
            try:
                os.remove(stale)
            except OSError:
                pass

    # Stage both temps before replacing anything: any failure up to the first
    # replace leaves the previous payload+manifest pair fully intact.
    stage = "write"
    parked: list[int] = []
    handler_installed = False
    previous = None
    result: dict[str, Any] = {}
    try:
        try:
            if max_bytes < len(_SNAPSHOT_MAGIC):
                # Even the header alone busts the cap: keep the committed-payload <= cap invariant.
                return {"error": "write failed: snapshot exceeds aggregate snapshot size cap"}
            fh, tmp = stage_temp(path, "wb")
            with fh:
                # Single pass: each variable is dill-serialized exactly once, streamed
                # into the staged temp. The record header is charged against the aggregate
                # cap up front, so a completed record can never overflow it (no prefix re-dump).
                total = fh.write(_SNAPSHOT_MAGIC)
                for name in list(ns.keys()):
                    if name.startswith("_") or name in _ALWAYS_SKIP:
                        continue
                    value = ns.get(name, missing)
                    if value is missing:
                        # A background thread deleted the name after the key listing.
                        skipped.append({"name": name, "reason": "deleted during snapshot"})
                        continue
                    encoded = name.encode("utf-8")
                    # Record header: 4-byte name length + 8-byte blob length, plus the name itself.
                    budget = max_bytes - total - 12 - len(encoded)
                    # Prune mode measures at the full per-variable cap: only that cap decides
                    # pruned-ness, and the write always re-measures — in-place mutation
                    # defeats any name-based size tracking from an earlier dump.
                    limit = (
                        max_variable_bytes if prune_oversized else min(max_variable_bytes, budget)
                    )
                    buffer = io.BytesIO()
                    try:
                        dill.dump(value, _CappedWriter(buffer, limit))
                        blob = buffer.getvalue()
                    except _SnapshotSizeLimitExceeded:
                        if not prune_oversized and budget < max_variable_bytes:
                            skipped.append(
                                {"name": name, "reason": "exceeds aggregate snapshot size cap"}
                            )
                        else:
                            skipped.append(
                                {"name": name, "reason": "exceeds per-variable snapshot size cap"}
                            )
                            oversized.append(name)
                        continue
                    except Exception as err:
                        skipped.append(
                            {
                                "name": name,
                                "reason": f"{type(err).__name__}: {_safe_str(err)[:200]}",
                            }
                        )
                        continue
                    if total + 12 + len(encoded) + len(blob) > max_bytes:
                        # Only reachable in prune mode, where the measurement cap
                        # ignores the budget.
                        skipped.append(
                            {"name": name, "reason": "exceeds aggregate snapshot size cap"}
                        )
                        continue
                    fh.write(len(encoded).to_bytes(4, "little"))
                    fh.write(encoded)
                    fh.write(len(blob).to_bytes(8, "little"))
                    fh.write(blob)
                    total += 12 + len(encoded) + len(blob)
                    saved.append(name)
                saved.sort()
                pruned = (
                    sorted(name for name in oversized if name in ns) if prune_oversized else []
                )
                manifest = {
                    "version": 1,
                    "savedNames": saved,
                    "skipped": skipped,
                    "pruned": pruned,
                    "bytes": total,
                    "pythonVersion": sys.version.split()[0],
                    "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
                }
                stage = "manifest write"
                fh, manifest_tmp = stage_temp(manifest_path, "w")
                with fh:
                    json.dump(manifest, fh)
        except BaseException as err:
            if not isinstance(err, Exception):
                raise  # e.g. KeyboardInterrupt: clean up (outer finally), then propagate
            return {"error": f"{stage} failed: {err}"}

        # A SIGINT-raised KeyboardInterrupt anywhere from the first commit through the
        # last cleanup removal would desync payload/manifest/namespace or misreport a
        # committed snapshot: park SIGINT until the end; it is consumed, see below.
        previous = signal.signal(signal.SIGINT, lambda signum, frame: parked.append(signum))
        handler_installed = True
        try:
            os.replace(tmp, path)
        except OSError as err:
            return {"error": f"write failed: {err}"}
        try:
            os.replace(manifest_tmp, manifest_path)
        except OSError as err:
            # Fail before the prune deletions so a bad manifest path never destroys state.
            return {"error": f"manifest write failed: {err}"}
        for name in pruned:
            ns.pop(name, None)
        result = {"saved": saved, "skipped": skipped, "pruned": pruned, "bytes": total}
        # Publish while still parked: a later KeyboardInterrupt into this task
        # finds the committed result (see _handle_state).
        if committed is not None:
            committed.append(result)
    finally:
        # The one guaranteed cleanup point (unique owned names: after a successful
        # commit the renamed temps no longer exist, so this is a no-op). It runs with
        # SIGINT still parked; the nested finally makes the restore the guaranteed
        # last action even when cleanup itself fails.
        try:
            discard_temps()
        finally:
            if handler_installed:
                signal.signal(signal.SIGINT, previous)
                # The parked SIGINT is consumed, not re-raised: with the manifest committed and
                # the namespace pruned, the destructive snapshot has succeeded, and re-raising
                # would misreport it as failed and risk the host discarding the only copy of
                # the pruned variables. The interrupt targeted this now-complete request.
    return result


def _restore_state(
    ns: dict[str, Any], path: str, committed: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    if not os.path.exists(path):
        return {"restored": [], "failed": [], "reason": "snapshot not found"}
    try:
        import dill  # ty: ignore[unresolved-import]  # optional dep; absence degrades to an error reply
    except Exception as err:
        return {"error": f"dill unavailable: {err}"}
    try:
        with open(path, "rb") as fh:
            if fh.read(len(_SNAPSHOT_MAGIC)) == _SNAPSHOT_MAGIC:
                payload = _read_snapshot_records(fh)
            else:
                # Legacy: one dill-pickled dict; old snapshot files must keep restoring.
                fh.seek(0)
                payload = dill.load(fh)
    except Exception as err:
        return {"error": f"load failed: {_safe_str(err)}"}
    if not isinstance(payload, dict):
        return {"error": "corrupt snapshot: not a dict"}

    staged: dict[str, Any] = {}
    failed: list[dict[str, str]] = []
    for name, blob in payload.items():
        if name in _RESTORE_SKIP:
            continue
        try:
            staged[name] = dill.loads(blob)
        except Exception as err:
            failed.append(
                {"name": name, "reason": f"{type(err).__name__}: {_safe_str(err)[:200]}"}
            )
    result = {"restored": sorted(staged), "failed": failed}
    # Park SIGINT across the whole apply so it is all-or-nothing; the parked
    # interrupt is consumed by the commit (as in snapshot).
    previous = signal.signal(signal.SIGINT, lambda signum, frame: None)
    try:
        for name, value in staged.items():
            ns[name] = value
            # Publish while still parked: a later KeyboardInterrupt into this task
            # finds the committed result (see _handle_state).
        if committed is not None:
            committed.append(result)
    finally:
        signal.signal(signal.SIGINT, previous)
    return result


async def _handle_state(req: dict[str, Any], ns: dict[str, Any]) -> None:
    """Run snapshot/restore as an interruptible task and reply in the done event."""
    import asyncio

    rid = req["id"]
    committed: list[dict[str, Any]] = []

    async def run() -> dict[str, Any]:
        if req["type"] == "snapshot":
            prune = req.get("prune_oversized", False)
            if not isinstance(prune, bool):
                return {"error": "prune_oversized must be a boolean"}
            for field in ("max_bytes", "max_variable_bytes"):
                # Any present value must be a non-negative int; a JSON null is
                # not a valid way to ask
                # for the default, and a negative cap would prune every user variable from ns.
                if field in req and (
                    isinstance(req[field], bool)
                    or not isinstance(req[field], int)
                    or req[field] < 0
                ):
                    return {"error": f"{field} must be a non-negative integer"}
            # realpath resolves symlinks, so aliased paths cannot silently clobber the payload.
            if os.path.realpath(req["path"]) == os.path.realpath(req["manifest_path"]):
                return {"error": "path and manifest_path must differ"}
            return _snapshot_state(
                ns,
                req["path"],
                req["manifest_path"],
                req.get("max_bytes", DEFAULT_SNAPSHOT_MAX_BYTES),
                req.get("max_variable_bytes", DEFAULT_SNAPSHOT_MAX_VARIABLE_BYTES),
                prune,
                committed,
            )
        return _restore_state(ns, req["path"], committed)

    assert _loop is not None
    task = _loop.create_task(run())
    outcome: tuple[str, Any, dict[str, Any] | None] | None = None
    try:
        outcome = await _run_guarded(task, rid)
        _finish_request(rid)  # no post-run repr/drain: close the interrupt window now
    except KeyboardInterrupt:
        # A finishing-targeted SIGINT can raise anywhere between _run_guarded's
        # finally publishing _finishing_rid and _finish_request clearing it; the
        # handler only raises once _finishing_rid is set, so the task is already
        # complete (destructively so for a pruning snapshot). Consume the
        # interrupt and report the task's real outcome; escaping to the backstop
        # would misreport a committed snapshot as failed.
        _finish_request(rid)
        if outcome is None:
            # The KeyboardInterrupt pre-empted _run_guarded's return: recover
            # the completed task's outcome with _run_guarded's failure mapping.
            try:
                outcome = ("ok", task.result(), None)
            except asyncio.CancelledError as exc:
                event = (
                    _interrupt_event(rid, exc)
                    if _active["interrupted"]
                    else _error_event(rid, exc)
                )
                outcome = ("error", None, event)
            except BaseException as exc:
                outcome = ("error", None, _error_event(rid, exc))
    status, result, error = outcome
    if (
        committed
        and _active["interrupted"]
        and error is not None
        and error.get("ename") == "KeyboardInterrupt"
    ):
        # Recover only a protocol interrupt that landed after the commit; a
        # user KeyboardInterrupt keeps interrupted reporting.
        status, result, error = "ok", committed[0], None
    if status != "ok":
        reason = (
            "interrupted"
            if error and error.get("ename") == "KeyboardInterrupt"
            else (f"{error.get('ename')}: {error.get('evalue')}" if error else "failed")
        )
        _send({"event": "done", "id": rid, "status": "error", "reason": reason})
        return
    # run() only ever returns a result mapping; the "error" branch above already
    # covered the failure tuples, whose value slot is None.
    assert isinstance(result, dict)
    if "error" in result:
        _send({"event": "done", "id": rid, "status": "error", "reason": result["error"]})
        return
    _send({"event": "done", "id": rid, "status": "ok", **result})


def _list_names(ns: dict[str, Any]) -> list[str]:
    """User-defined top-level names, filtered like the snapshot."""
    # Non-string keys (globals()[1] = 1) are not user-listable names.
    return sorted(
        name
        for name in ns
        if isinstance(name, str) and not name.startswith("_") and name not in _ALWAYS_SKIP
    )


async def _handle_list_names(req: dict[str, Any], ns: dict[str, Any]) -> None:
    _send({"event": "done", "id": req["id"], "status": "ok", "names": _list_names(ns)})


async def _handle_request(
    handler: Callable[[dict[str, Any], dict[str, Any]], Awaitable[None]],
    req: dict[str, Any],
    ns: dict[str, Any],
) -> None:
    # Backstop: one broken request (e.g. RecursionError in compile) fails
    # alone, never the serve loop.
    try:
        await handler(req, ns)
    except BaseException as exc:
        rid = req["id"]
        with _interrupt_lock:
            # Only a still-inflight request (never reached _run_guarded, e.g. compile
            # failure) owns a parked interrupt; after _run_guarded finished it, a parked
            # "any" belongs to the next request and must survive.
            if rid in _inflight:
                _consume_pending_interrupt(rid)
            _finish_locked(rid)
        _send(_error_event(rid, exc))
        _send({"event": "done", "id": rid, "status": "error"})


# =================================================================================================
# Request parsing / serving
# =================================================================================================


async def _serve(queue: asyncio.Queue[dict[str, Any]], ns: dict[str, Any]) -> None:
    while True:
        req = await queue.get()
        # A cell (or a snapshot-restored prior handler) may have rebound SIGINT; the
        # protocol handler must own it before each request. Mid-cell rebinds remain
        # that cell's own problem for that cell only.
        signal.signal(signal.SIGINT, _sigint_handler)
        rtype = req.get("type")
        if rtype == "shutdown":
            rid = req.get("id")
            # Kill live bash children now; atexit would wait on parked executor threads.
            _kill_live_handles()
            if isinstance(rid, str):
                _send({"event": "done", "id": rid, "status": "ok"})
            return
        if rtype == "execute":
            await _handle_request(_handle_execute, req, ns)
        elif rtype in ("snapshot", "restore"):
            await _handle_request(_handle_state, req, ns)
        elif rtype == "list_names":
            await _handle_request(_handle_list_names, req, ns)


_REQUIRED_FIELDS = {
    "execute": ("id", "code"),
    "snapshot": ("id", "path", "manifest_path"),
    "restore": ("id", "path"),
    "list_names": ("id",),
    "shutdown": (),
}


def _protocol_error(message: str) -> None:
    _send(
        {
            "event": "error",
            "id": None,
            "ename": "ProtocolError",
            "evalue": message,
            "traceback": [],
        }
    )


def _handle_request_line(raw: bytes, queue: asyncio.Queue[dict[str, Any]]) -> None:
    assert _loop is not None
    req = json.loads(raw)
    if not isinstance(req, dict):
        raise ValueError("request is not a JSON object")
    rtype = req.get("type")
    if rtype == "interrupt":
        if "id" in req and not isinstance(req["id"], str):
            _protocol_error("interrupt request id must be a string")
            return
        _request_interrupt(req.get("id"))
        return
    if rtype == "host_reply":
        # Bypass the FIFO queue: the awaiting cell IS the in-flight
        # execute, so a queued reply would deadlock behind it.
        rid = req.get("id")
        data = req.get("data")
        if isinstance(rid, str) and data is not None:
            _resolve_host_reply(rid, data)
        else:
            _protocol_error("host_reply request needs string id and data")
        return
    if not isinstance(rtype, str) or rtype not in _REQUIRED_FIELDS:
        _protocol_error(f"unknown request type: {rtype!r}")
        return
    missing = [f for f in _REQUIRED_FIELDS[rtype] if not isinstance(req.get(f), str)]
    if missing:
        _protocol_error(f"{rtype} request needs string fields: {', '.join(missing)}")
        return
    if rtype in ("execute", "snapshot", "restore"):
        with _interrupt_lock:
            # A reused in-flight id would corrupt interrupt/finish bookkeeping.
            duplicate = req["id"] in _inflight
            if not duplicate:
                _inflight.add(req["id"])
        # The protocol write can block on backpressure: never send under the lock.
        if duplicate:
            _protocol_error(f"duplicate in-flight request id: {req['id']!r}")
            return
    if rtype == "shutdown":
        # No host reply follows a shutdown; a cell awaiting host_request
        # must fail now or it would block _serve from ever consuming this.
        _loop.call_soon_threadsafe(_fail_pending_host_requests)
    _loop.call_soon_threadsafe(queue.put_nowait, req)


def _read_requests(stdin_fd: int, queue: asyncio.Queue[dict[str, Any]]) -> None:
    assert _loop is not None
    with os.fdopen(stdin_fd, "rb") as stream:
        for raw in stream:
            raw = raw.strip()
            if not raw:
                continue
            try:
                # The whole per-line handling sits inside the backstop: hostile
                # input (RecursionError from pathological nesting, unhashable
                # field types, ...) must never kill the reader thread.
                _handle_request_line(raw, queue)
            except BaseException as err:
                _protocol_error(f"{type(err).__name__}: {_safe_str(err)}")
    # Host closed stdin: shut the runtime down.
    _loop.call_soon_threadsafe(_fail_pending_host_requests)
    _loop.call_soon_threadsafe(queue.put_nowait, {"type": "shutdown"})


def _resolve_owner_pid() -> int:
    raw = os.environ.get("VTX_KERNEL_OWNER_PID", "")
    try:
        owner = int(raw)
    except ValueError:
        owner = 0
    return owner if owner > 0 else os.getppid()


def _owner_alive_posix(owner: int, initial_ppid: int) -> bool:
    # Reparenting is the race-free parent-death signal when the owner is the
    # parent; the kill-0 probe covers an env-designated non-parent owner.
    if initial_ppid == owner and os.getppid() != initial_ppid:
        return False
    try:
        os.kill(owner, 0)
    except ProcessLookupError:
        return False
    except OSError:
        pass  # EPERM etc.: alive but unprobeable
    return True


def _wait_owner_windows(owner: int) -> None:
    # Blocks until the owner exits. os.kill(pid, 0) on Windows TERMINATES the
    # target, so an SYNCHRONIZE handle wait is the only sound probe.
    from ctypes import wintypes

    SYNCHRONIZE = 0x00100000
    INFINITE = 0xFFFFFFFF
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)  # ty: ignore[unresolved-attribute]  # Windows-only path
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k32.WaitForSingleObject.restype = wintypes.DWORD
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.CloseHandle.restype = wintypes.BOOL
    handle = k32.OpenProcess(SYNCHRONIZE, False, owner)
    if not handle:
        return  # already gone (or unprobeable): exit rather than run ownerless
    try:
        k32.WaitForSingleObject(handle, INFINITE)
    finally:
        k32.CloseHandle(handle)


def _owner_watchdog(owner: int, initial_ppid: int) -> None:
    if os.name == "nt":
        _wait_owner_windows(owner)
    else:
        while _owner_alive_posix(owner, initial_ppid):
            time.sleep(1.0)
    # Event-loop-independent by design: a synchronous cell monopolizes the
    # loop, so the queued EOF shutdown can never run; hard-exit from here.
    try:
        _kill_live_handles()
    except BaseException:
        pass
    os._exit(1)


def _start_owner_watchdog() -> None:
    threading.Thread(
        target=_owner_watchdog, args=(_resolve_owner_pid(), os.getppid()), daemon=True
    ).start()


_pump_out: _Pump
_pump_err: _Pump


def _setup_fds() -> int:
    """Reserve stdout for the protocol; route fds 1/2 through captured pipes."""
    global _protocol_fd, _pump_out, _pump_err
    _protocol_fd = os.dup(1)
    os.set_inheritable(_protocol_fd, False)
    out_r, out_w = os.pipe()
    err_r, err_w = os.pipe()
    os.dup2(out_w, 1)
    os.dup2(err_w, 2)
    os.close(out_w)
    os.close(err_w)
    sys.stdout = _TaggedWriter("stdout", fallback_fd=os.dup(1))
    sys.stderr = _TaggedWriter("stderr", fallback_fd=os.dup(2))
    stdin_fd = os.dup(0)
    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, 0)
    os.close(devnull)
    sys.stdin = open(os.devnull)  # user input() sees EOF, never protocol frames
    _pump_out = _Pump(out_r, 1, "stdout")
    _pump_err = _Pump(err_r, 2, "stderr")
    return stdin_fd


# =================================================================================================
# VTX context + pre-bound helpers (RLMContext, skills, legacy sync helpers)
# =================================================================================================


@dataclass
class RLMContext:
    """Rich Python context object exposed in the persistent REPL namespace.

    Allows the model to inspect the conversation as a variable (`context`),
    slice messages, search past history, check token usage, and examine metadata.
    """

    session_id: str = "default"
    cwd: str = ""
    model: str = ""
    system_prompt: str = ""
    messages: list[dict[str, Any]] = field(default_factory=list)
    tokens: dict[str, Any] = field(default_factory=dict)
    custom_metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def last_message(self) -> dict[str, Any] | None:
        """Return the most recent message in the session."""
        return self.messages[-1] if self.messages else None

    @property
    def last_user_message(self) -> dict[str, Any] | None:
        """Return the most recent user prompt message."""
        for msg in reversed(self.messages):
            if msg.get("role") == "user":
                return msg
        return None

    def get_history(
        self, limit: int | None = None, role: str | None = None
    ) -> list[dict[str, Any]]:
        """Filter conversation history by role or limit."""
        msgs = self.messages
        if role:
            msgs = [m for m in msgs if m.get("role") == role]
        if limit is not None:
            msgs = msgs[-limit:]
        return msgs

    def search(self, pattern: str) -> list[dict[str, Any]]:
        """Search message text contents matching string or regex pattern."""
        regex = re.compile(pattern, re.IGNORECASE)
        results = []
        for msg in self.messages:
            content = msg.get("content", "")
            if isinstance(content, list):
                content_str = " ".join(str(p) for p in content)
            else:
                content_str = str(content)
            if regex.search(content_str):
                results.append(msg)
        return results

    @property
    def code_history(self) -> list[str]:
        """Return all code snippets written across conversation turns and cell executions."""
        snippets: list[str] = []
        # First gather from message history (any tool_calls to ipython/bash or python codeblocks)
        for msg in self.messages:
            tool_calls = msg.get("tool_calls") or []
            for tc in tool_calls:
                if tc.get("name") in ("ipython", "bash"):
                    args = tc.get("arguments") or {}
                    code = args.get("code") or args.get("command")
                    if code and code not in snippets:
                        snippets.append(code)
        # Also include all cells executed in this runtime session from In[1:]
        for c in In[1:]:
            if c and c not in snippets:
                snippets.append(c)
        return snippets

    def get_code(self, index: int = -1) -> str:
        """Get a previous code snippet by index (default -1 for most recent)."""
        history = self.code_history
        if not history:
            return ""
        try:
            return history[index]
        except IndexError:
            return ""

    def search_code(self, pattern: str) -> list[str]:
        """Search previous code snippets for matching text or regex."""
        regex = re.compile(pattern, re.IGNORECASE)
        return [code for code in self.code_history if regex.search(code)]

    def __repr__(self) -> str:
        msg_count = len(self.messages)
        cells_count = max(0, len(In) - 1)
        return (
            f"<RLMContext session_id={self.session_id!r} cwd={self.cwd!r} "
            f"model={self.model!r} messages={msg_count} cells={cells_count}>"
        )


def _update_context_in_namespace(ctx_dict: dict[str, Any] | None) -> None:
    """Update or initialize the `context` variable in the global REPL namespace."""
    if not ctx_dict:
        if "context" not in _namespace:
            _namespace["context"] = RLMContext(cwd=os.getcwd())
        return

    _namespace["context"] = RLMContext(
        session_id=ctx_dict.get("session_id", "default"),
        cwd=ctx_dict.get("cwd", os.getcwd()),
        model=ctx_dict.get("model", ""),
        system_prompt=ctx_dict.get("system_prompt", ""),
        messages=ctx_dict.get("messages", []),
        tokens=ctx_dict.get("tokens", {}),
        custom_metadata=ctx_dict.get("custom_metadata", {}),
    )
    _init_python_skills(ctx_dict.get("cwd"), ctx_dict.get("python_skills"))


#: ``(name, object)`` for every helper the kernel binds, populated by
#: :func:`_init_builtin_helpers`. Module-level so the prompt's generated helper
#: reference can be checked against it; see
#: ``tests/test_rlm_helper_reference.py``.
_HELPERS: tuple[tuple[str, Any], ...] = ()


def _init_builtin_helpers() -> None:
    """Initialize pre-bound helpers in the REPL namespace if not already present."""
    from .bash import bash as _bash

    def run_bash(command: str, timeout: float = 180.0) -> str:
        """Blocking shell command: returns combined stdout+stderr as a string.

        Backed by the same background handle as ``bash()``; blocks the calling
        cell (never the completion machinery) until the command exits or
        ``timeout`` elapses, then returns whatever output was captured.
        """
        handle = _bash(command)
        finished = handle._done.wait(timeout)
        output = handle.output()
        if not finished:
            return (
                output
                + f"\n[run_bash timed out after {timeout:.0f}s; command still running "
                + f"(pid={handle.pid})]"
            )
        result = handle.poll()
        if result is not None and result.output:
            return result.output
        return output

    def read_file(path: str, offset: int = 0, limit: int = 2000) -> str:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        selected = lines[offset : offset + limit]
        text = "".join(selected)
        # Byte cap (prime parity: 2000 lines AND 50KB): a long-line file
        # would otherwise dump up to the 64KB stream cap into context.
        if len(text.encode("utf-8", errors="replace")) > _READ_FILE_MAX_BYTES:
            budget = _READ_FILE_MAX_BYTES
            kept: list[str] = []
            size = 0
            for line in selected:
                size += len(line.encode("utf-8", errors="replace"))
                if size > budget:
                    break
                kept.append(line)
            text = "".join(kept)
            total_lines = len(lines)
            text += (
                f"\n[... read truncated at {_READ_FILE_MAX_BYTES // 1024}KB "
                f"({len(kept)}/{len(selected)} lines shown, file has {total_lines} lines); "
                f"re-read with offset={offset + len(kept)} and a smaller limit ...]"
            )
        elif offset + limit < len(lines):
            text += (
                f"\n[... {len(lines) - offset - limit} more lines; "
                f"re-read with offset={offset + limit} ...]"
            )
        return text

    def write_file(path: str, content: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    def edit_file(path: str, old: str, new: str, replace_all: bool = False) -> str:
        with open(path, encoding="utf-8") as f:
            data = f.read()
        if old not in data:
            raise ValueError(f"Target content not found in {path}")
        count = -1 if replace_all else 1
        new_data = data.replace(old, new, count)
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_data)
        return f"Edited {path}"

    def run_code(code_str: str) -> Any:
        """Execute code in the REPL namespace and return its last expression value.

        A trailing coroutine is scheduled on the kernel loop and returned as a
        task, so ``await run_code(...)`` works when the snippet needs ``await``.
        """
        flags = ast.PyCF_ALLOW_TOP_LEVEL_AWAIT
        tree = None
        trailing = None
        with contextlib.suppress(SyntaxError):
            tree = ast.parse(code_str, mode="exec")
        if tree is not None and tree.body and isinstance(last := tree.body[-1], ast.Expr):
            tree.body.pop()
            trailing = ast.Expression(last.value)

        val = None
        if tree is not None and tree.body:
            c = compile(tree, "<dynamic-cell>", "exec", flags=flags)
            res = eval(c, _namespace)  # eval() handles top-level await in CPython
            if inspect.iscoroutine(res):
                val = _schedule(res)
        if trailing is not None:
            c_expr = compile(trailing, "<dynamic-cell>", "eval", flags=flags)
            val = eval(c_expr, _namespace)
            if inspect.iscoroutine(val):
                val = _schedule(val)
            elif val is not None:
                _namespace["_"] = val
        return val

    def _schedule(coro: Any) -> Any:
        import asyncio

        try:
            return asyncio.ensure_future(coro)
        except RuntimeError:
            return asyncio.run(coro)

    def rerun(index: int = -1) -> Any:
        """Re-run a previously executed cell or code snippet by index (default: last)."""
        ctx: RLMContext | None = _namespace.get("context")
        code = ctx.get_code(index) if ctx else ""
        if not code and len(In) > 1:
            code = In[index]
        if not code:
            raise ValueError(f"No previous code found at index {index}")
        return run_code(code)

    def find_tools(query: str, limit: int = 8) -> list[dict[str, Any]]:
        """Search the callable tool surface by keyword; best matches first.

        BM25 over name, description, and parameter names, so a task description
        is enough to find the tool. Returns ``{name, description}`` dicts, best
        first; an empty list means nothing matched.
        """
        return _search_tools(query, limit)

    def describe_tool(name: str) -> dict[str, Any] | None:
        """One tool's full declaration: description and parameter schema.

        The counterpart to :func:`find_tools`: search narrows the field, this
        gives the exact argument shape so the call is right first time. Returns
        ``None`` when no such tool is callable.
        """
        return _describe_tool(name)

    def web_search(query: str, num_results: int = 8) -> str:
        """Web search via the main-process tool bridge."""
        return call_tool("web_search", query=query, num_results=num_results)

    def goal_get() -> dict[str, Any]:
        """Get the current focused goal via the main-process tool bridge."""
        return call_tool("goal", action="get")

    def goal_update(**kwargs: Any) -> dict[str, Any]:
        """Update the current focused goal via the main-process tool bridge."""
        return call_tool("goal", action="update", **kwargs)

    def goal_set_tasks(tasks: list[dict[str, Any]]) -> dict[str, Any]:
        """Set tasks for the current focused goal via the main-process tool bridge."""
        return call_tool("goal", action="set_tasks", tasks=tasks)

    # One table drives both the bindings and the shadowing guard, so a helper
    # cannot be bound without being protected (or the reverse). ``call_tool``,
    # ``emit``, and ``host_request`` are module-level rather than closures, but
    # they are protected exactly like the rest. Module-level so the prompt
    # builder and the drift test can see what is actually bound.
    global _HELPERS
    _HELPERS = (
        ("bash", _bash),
        ("run_bash", run_bash),
        ("read_file", read_file),
        ("write_file", write_file),
        ("edit_file", edit_file),
        ("run_code", run_code),
        ("rerun", rerun),
        ("find_tools", find_tools),
        ("describe_tool", describe_tool),
        ("web_search", web_search),
        ("goal_get", goal_get),
        ("goal_update", goal_update),
        ("goal_set_tasks", goal_set_tasks),
        ("call_tool", call_tool),
        ("emit", emit),
        ("host_request", host_request),
    )
    for _name, _helper in _HELPERS:
        _namespace.setdefault(_name, _helper)
    _protect_helpers(_namespace, tuple(name for name, _ in _HELPERS))


#: Name -> the object bound to it when the helpers were installed. A cell that
#: reassigns one of these silently disables the real helper for every later
#: cell, because the namespace is the same dict for the life of the kernel and
#: nothing re-binds it (the bridge, `bash`, and the file helpers are only
#: reachable through these names). Rebinding is therefore reverted and reported
#: instead of accepted: a model that meant to shadow one almost always meant to
#: define a local, and a silently broken `call_tool` is invisible until a much
#: later cell fails to reach a tool at all.
_protected_helpers: dict[str, Any] = {}


def _protect_helpers(ns: dict[str, Any], names: tuple[str, ...]) -> None:
    for name in names:
        if name in ns:
            _protected_helpers[name] = ns[name]
        else:
            # Never record a name that is not bound. A ``None`` here would be
            # indistinguishable from a helper the cell deliberately replaced
            # with ``None``, and restoring it would delete the real binding.
            _protected_helpers.pop(name, None)


def _restore_shadowed_helpers(ns: dict[str, Any]) -> list[str]:
    """Undo helper rebinding and name what was restored. Returns the names.

    A name that was never bound is not guarded, so this can only ever put back
    an object that was really there.
    """
    restored: list[str] = []
    for name, original in _protected_helpers.items():
        if ns.get(name) is not original:
            ns[name] = original
            restored.append(name)
    return restored


_python_skills_key: tuple[Any, ...] | None = None


def _init_python_skills(
    cwd: str | None = None, python_skills: list[dict[str, Any]] | None = None
) -> None:
    """Idempotent: cells re-send the skill inventory on every execute, but
    re-importing (and reloading) every skill module per cell would dominate
    cell latency. A changed inventory or cwd re-runs the discovery."""
    global _python_skills_key
    key = (
        cwd,
        tuple(
            sorted(
                (str(entry.get("import_name")), str(entry.get("package_path")))
                for entry in python_skills or []
            )
        ),
    )
    if key == _python_skills_key:
        return
    _python_skills_key = key
    _discover_python_skills(cwd, python_skills)


def _discover_python_skills(
    cwd: str | None = None, python_skills: list[dict[str, Any]] | None = None
) -> None:
    """Discover Python-backed skills and bind their callable modules in the REPL namespace.

    Follows the Prime Agent Python skills protocol:
    - Explicit entries from the host (bundled skills the directory walk cannot see)
    - Skills with pyproject.toml and src/<import_name>/__init__.py found on disk
    - Appends src/ to sys.path
    - Imports and wraps each module via `vtx.skill.wrap_skill_module`
    - Binds the import name in `_namespace`
    """
    from vtx.skill import FailedSkillModule, wrap_skill_module

    # Host-provided packages (bundled VTX skills) first: they are not on disk
    # under a scanned skills directory.
    for entry in python_skills or []:
        import_name = str(entry.get("import_name") or "")
        package_path = entry.get("package_path")
        if not import_name or not package_path:
            continue
        src_dir = Path(str(package_path)) / "src"
        if not (src_dir / import_name / "__init__.py").is_file():
            continue
        src_str = str(src_dir.resolve())
        if src_str not in sys.path:
            sys.path.insert(0, src_str)
        try:
            module = importlib.import_module(import_name)
            module = importlib.reload(module)
            _namespace[import_name] = wrap_skill_module(module)
        except Exception as exc:
            _namespace[import_name] = FailedSkillModule(import_name, exc)

    target_cwd = Path(cwd or os.getcwd()).resolve()

    # Find skill directories from project and user locations
    skill_dirs: list[Path] = []

    # 1. Project .agents/skills/ walking up to git root or filesystem root
    curr = target_cwd
    while True:
        candidate = curr / ".agents" / "skills"
        if candidate.is_dir():
            skill_dirs.append(candidate)
        if (curr / ".git").is_dir() or curr.parent == curr:
            break
        curr = curr.parent

    # 2. User ~/.agents/skills/
    user_skills = (Path.home() / ".agents" / "skills").resolve()
    if user_skills.is_dir() and user_skills not in skill_dirs:
        skill_dirs.append(user_skills)

    # 3. User ~/.vtx/skills/
    vtx_skills = (Path.home() / ".vtx" / "skills").resolve()
    if vtx_skills.is_dir() and vtx_skills not in skill_dirs:
        skill_dirs.append(vtx_skills)

    for base_dir in skill_dirs:
        try:
            for skill_folder in base_dir.iterdir():
                if not skill_folder.is_dir() or skill_folder.name.startswith("."):
                    continue
                pyproject = skill_folder / "pyproject.toml"
                if not pyproject.is_file():
                    continue
                import_name = skill_folder.name.replace("-", "_")
                if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", import_name):
                    continue
                src_dir = skill_folder / "src"
                pkg_init = src_dir / import_name / "__init__.py"
                if not pkg_init.is_file():
                    continue

                # Add src_dir to sys.path if not present
                src_str = str(src_dir.resolve())
                if src_str not in sys.path:
                    sys.path.insert(0, src_str)

                # Import and wrap module
                try:
                    module = importlib.import_module(import_name)
                    # Force reload if module was already imported to pick up any changes
                    module = importlib.reload(module)
                    wrapped = wrap_skill_module(module)
                    _namespace[import_name] = wrapped
                except Exception as exc:
                    _namespace[import_name] = FailedSkillModule(import_name, exc)
        except Exception:
            pass


def _bind_rlm_namespace() -> None:
    """Expose the `rlm` object (spawn/harness/collect/...) in the kernel namespace."""
    try:
        from vtx.ai.agent import rlm as rlm_package
    except Exception:  # pragma: no cover - package init must never kill the kernel
        return
    _namespace.setdefault("rlm", rlm_package.rlm)
    _namespace.setdefault("harness", rlm_package.harness)


def main() -> None:
    global _loop, _serve_task, _namespace
    stdin_fd = _setup_fds()
    _start_owner_watchdog()

    # Alias the executing modules so an in-cell `from ... import ...` binds the
    # live modules, not second copies. Prime-style `import rlm` keeps working.
    sys.modules.setdefault("vtx.ai.agent.rlm.repl", sys.modules[__name__])
    sys.modules.setdefault("rlm.repl", sys.modules[__name__])
    try:
        from vtx.ai.agent import rlm as rlm_pkg

        sys.modules.setdefault("rlm", rlm_pkg)
    except Exception:
        pass
    # A real __main__ module makes dill pickle user functions/classes by value.
    user_module = types.ModuleType("__main__")
    user_module.__dict__["__builtins__"] = __builtins__
    sys.modules["__main__"] = user_module
    _namespace = user_module.__dict__
    _namespace["In"] = In
    _namespace["Out"] = Out
    _namespace["_ih"] = In
    _namespace["_oh"] = Out

    _init_builtin_helpers()
    _init_python_skills()
    _bind_rlm_namespace()
    _update_context_in_namespace(None)

    _send({"event": "ready", "protocol": PROTOCOL_VERSION, "python": platform.python_version()})

    # The event-loop stack (asyncio plus its ssl, concurrent.futures, and
    # logging imports) is the heaviest part of this module's boot chain; load
    # it after the ready event so kernel startup stays lean. The loop, reader
    # thread, and serve task all come up here before the host's first request
    # can be served, and every function that references asyncio runs only
    # after this point.
    import asyncio

    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    threading.Thread(target=_read_requests, args=(stdin_fd, queue), daemon=True).start()

    _serve_task = _loop.create_task(_serve(queue, user_module.__dict__))
    # _sigint_handler has no task to target before serving starts, so installing
    # it earlier would silently swallow a Ctrl-C during this boot window; the
    # default handler must stay in charge until the loop and serve task exist.
    signal.signal(signal.SIGINT, _sigint_handler)
    # A KeyboardInterrupt escaping a cell or background task stops
    # run_until_complete; the interrupt is already recorded, so resume serving.
    while not _serve_task.done():
        try:
            _loop.run_until_complete(_serve_task)
        except KeyboardInterrupt:
            continue
    _loop.close()


if __name__ == "__main__":
    main()

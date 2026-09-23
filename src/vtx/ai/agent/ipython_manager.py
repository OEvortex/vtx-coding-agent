"""Persistent RLM REPL kernel manager.

Maintains long-lived ``python -m vtx.ai.agent.ipython_runtime`` subprocesses
keyed by session id. Each subprocess executes snippets sequentially, preserving
imports, variables, and side effects across calls. Communication uses the
newline-delimited protocol documented next to Prime Agent's ``rlm.repl``:
requests ``execute`` / ``interrupt`` / ``host_reply`` / ``snapshot`` /
``restore`` / ``list_names`` / ``shutdown`` and events ``ready`` / ``stdout`` /
``stderr`` / ``result`` / ``display`` / ``host_request`` / ``error`` / ``done``.

Ported from Prime Agent (MIT) — https://github.com/PrimeIntellect-ai/prime-agent
"""

from __future__ import annotations

import asyncio
import codecs
import contextlib
import json
import os
import shutil
import sys
import time
import uuid
from asyncio.subprocess import Process
from collections.abc import Callable, Coroutine
from typing import Any

from vtx.core.bytes_util import truncate_bytes

_MAX_INACTIVITY_SECONDS = 600
_OUTPUT_TRUNCATE_BYTES = 1_048_576  # 1 MiB per tool call
_DEFAULT_POOL_SIZE = 4

PROTOCOL_VERSION = 3
_READY_TIMEOUT_SECONDS = 30.0
# On abort the host waits this long for the kernel's `done` before it gives up
# and reports the cell as aborted; the real `done` may still arrive later.
_ABORT_GRACE_SECONDS = 1.0
# Per-execution stream caps: the kernel ships whole lines, so truncation
# happens here, once, with the marker the model is told to expect.
_MAX_STREAM_CHARS = 65536
_STREAM_TRUNCATION_MARKER = "\n[... output truncated at 65536 chars ...]"


class IpythonKernel:
    """One persistent RLM REPL runtime subprocess."""

    def __init__(
        self,
        kernel_id: str,
        cwd: str,
        *,
        python: str | None = None,
        env: dict[str, str] | None = None,
        tool_executor: Callable[[str, dict[str, Any]], Coroutine[Any, Any, Any]] | None = None,
        host_dispatcher: Callable[..., Coroutine[Any, Any, dict[str, Any]]] | None = None,
    ) -> None:
        self.kernel_id = kernel_id
        self.cwd = cwd
        self.python = python or sys.executable
        self._env = env or {}
        # Test seam: by default every ``host_request`` goes to the real bridge
        # in ``vtx.ai.agent.rlm.host``.
        self._host_dispatcher = host_dispatcher
        self._process: Process | None = None
        self._last_activity = time.monotonic()
        self._execution_lock = asyncio.Lock()
        self._closed = False
        self._reader_task: asyncio.Task[None] | None = None
        self._queue: asyncio.Queue[str | None] | None = None
        self._output_buffer = ""
        self._result_repr: str | None = None
        self._pending_done: dict[str, asyncio.Event] = {}
        self._tool_executor = tool_executor
        self._ready_event = asyncio.Event()
        self._protocol: int | None = None
        self._session_id: str | None = None
        self._session_dir: str | None = None

    def set_session(self, session_id: str | None, session_dir: str | None = None) -> None:
        """Bind the session identity used for host-bridge routing and kernel env.

        Must be called before the first ``execute`` (the env is fixed at spawn).
        """
        self._session_id = session_id
        self._session_dir = session_dir

    def _kernel_env(self) -> dict[str, str]:
        env = {**dict(os.environ), **self._env}
        env.setdefault("VTX_KERNEL_OWNER_PID", str(os.getpid()))
        # The host always injects an absolute shell path so a repo-controlled
        # PATH can never decide which shell `bash()` runs.
        env.setdefault("VTX_BASH_SHELL", shutil.which("bash") or "/bin/sh")
        try:
            from vtx.core.paths import get_config_dir

            harness_dir = get_config_dir() / "harness"
            env.setdefault("VTX_GLOBAL_HARNESS_STATE_DIR", str(harness_dir))
            if self._session_dir:
                env.setdefault("VTX_SESSION_DIR", self._session_dir)
                env.setdefault("VTX_HARNESS_STATE_DIR", os.path.join(self._session_dir, "harness"))
        except Exception:
            pass
        return env

    async def start(self) -> None:
        if self._process and self._process.returncode is None:
            return
        self._ready_event = asyncio.Event()
        self._protocol = None
        self._process = await asyncio.create_subprocess_exec(
            self.python,
            "-m",
            "vtx.ai.agent.ipython_runtime",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.cwd,
            env=self._kernel_env(),
        )
        self._last_activity = time.monotonic()
        self._queue = asyncio.Queue()
        self._reader_task = asyncio.create_task(self._read_events())
        try:
            await asyncio.wait_for(self._ready_event.wait(), timeout=_READY_TIMEOUT_SECONDS)
        except TimeoutError:
            raise RuntimeError(
                f"RLM kernel did not become ready within {_READY_TIMEOUT_SECONDS:.0f}s"
            ) from None
        if self._protocol != PROTOCOL_VERSION:
            raise RuntimeError(
                f"Kernel runtime speaks protocol {self._protocol}, expected {PROTOCOL_VERSION}. "
                "Reinstall or upgrade vtx."
            )

    async def _read_events(self) -> None:
        assert self._process and self._process.stdout is not None
        buffer = ""
        decoder = None
        while not self._closed:
            try:
                raw = await self._process.stdout.read(4096)
            except Exception:
                break
            if not raw:
                break
            if isinstance(raw, str):
                text = raw
            else:
                if decoder is None:
                    try:
                        text = raw.decode("utf-8")
                    except UnicodeDecodeError:
                        decoder = codecs.getincrementaldecoder("utf-8")("replace")
                        text = decoder.decode(raw)
                else:
                    text = decoder.decode(raw)
            buffer += text
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                if self._is_ready_line(line):
                    continue
                if self._queue is not None:
                    await self._queue.put(line.strip())
        if self._queue is not None:
            await self._queue.put(None)

    def _is_ready_line(self, line: str) -> bool:
        """Consume the handshake line instead of queueing it."""
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return False
        if not isinstance(event, dict) or event.get("event") != "ready":
            return False
        protocol = event.get("protocol")
        self._protocol = protocol if isinstance(protocol, int) else None
        self._ready_event.set()
        return True

    async def _next_event(self, timeout: float) -> dict[str, Any] | None:
        if self._queue is None:
            return None
        try:
            raw = await asyncio.wait_for(self._queue.get(), timeout=timeout)
        except TimeoutError:
            return None
        if raw is None:
            return None
        if not raw:
            return None
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return event if isinstance(event, dict) else None

    async def _send(self, payload: dict[str, Any]) -> bool:
        """Write one request line; ``False`` when the pipe is gone."""
        if self._process is None or self._process.stdin is None:
            return False
        line = json.dumps(payload, separators=(",", ":")) + "\n"
        try:
            self._process.stdin.write(line.encode("utf-8"))
            await self._process.stdin.drain()
            return True
        except (ConnectionResetError, BrokenPipeError, ProcessLookupError):
            return False

    async def _handle_host_request(
        self,
        event: dict[str, Any],
        tool_executor: Callable[[str, dict[str, Any]], Coroutine[Any, Any, Any]] | None,
    ) -> None:
        """Dispatch one kernel->host request and always write a ``host_reply``."""
        rid = event.get("id")
        data = event.get("data")
        if not isinstance(rid, str) or not isinstance(data, dict):
            await self._send(
                {
                    "type": "host_reply",
                    "id": rid,
                    "data": {"status": "error", "error": "malformed host_request"},
                }
            )
            return
        try:
            dispatch = self._host_dispatcher
            if dispatch is None:
                from vtx.ai.agent.rlm.host import dispatch_host_request

                dispatch = dispatch_host_request
            reply = await dispatch(data, tool_executor=tool_executor, session_id=self._session_id)
        except Exception as exc:
            reply = {"status": "error", "error": f"host bridge unavailable: {exc}"}
        if not isinstance(reply, dict) or reply.get("status") not in ("ok", "error"):
            reply = {"status": "error", "error": "host bridge returned a malformed reply"}
        await self._send({"type": "host_reply", "id": rid, "data": reply})

    async def execute(
        self,
        code: str,
        on_output: Callable[[str], Coroutine[Any, Any, None]] | None = None,
        *,
        timeout: float = 180.0,
        context: dict[str, Any] | None = None,
        tool_executor: Callable[[str, dict[str, Any]], Coroutine[Any, Any, Any]] | None = None,
    ) -> tuple[str, bool]:
        """Run ``code`` in the kernel.

        Returns ``(output, errored)`` — ``errored`` is ``True`` when the cell
        raised, was interrupted, or timed out. ``output`` is the assembled
        stdout/stderr/result/traceback text (or a short status sentence when
        the cell produced nothing).
        """
        if self._closed:
            return ("REPL kernel is closed. Start a new session.", True)
        if tool_executor is not None:
            self._tool_executor = tool_executor
        try:
            await self.start()
        except Exception as exc:
            return (f"REPL kernel failed to start: {exc}", True)
        if self._process is None or self._process.stdin is None:
            return ("REPL kernel failed to start.", True)

        async with self._execution_lock:
            self._last_activity = time.monotonic()

            rid = uuid.uuid4().hex
            done_event = asyncio.Event()
            self._pending_done[rid] = done_event

            request: dict[str, Any] = {"type": "execute", "id": rid, "code": code.rstrip()}
            if context is not None:
                request["context"] = context
            if not await self._send(request):
                self._pending_done.pop(rid, None)
                return ("REPL kernel connection was lost and could not be restored.", True)

            try:
                return await self._wait_output(
                    rid, done_event, on_output=on_output, timeout=timeout
                )
            finally:
                self._pending_done.pop(rid, None)

    async def _wait_output(
        self,
        rid: str,
        done_event: asyncio.Event,
        *,
        on_output: Callable[[str], Coroutine[Any, Any, None]] | None = None,
        timeout: float,
    ) -> tuple[str, bool]:
        start = time.monotonic()
        errored = False
        timed_out = False
        stdout_parts: list[str] = []
        stderr_parts: list[str] = []
        result_text: str | None = None
        error_parts: list[str] = []
        display_parts: list[str] = []
        stdout_chars = 0
        stderr_chars = 0
        stdout_capped = False
        stderr_capped = False

        async def stream(tag: str, text: str) -> None:
            if on_output is not None and text:
                await on_output(f"{tag}{text}")

        def cap(
            parts: list[str], chars: int, text: str, capped: bool
        ) -> tuple[list[str], int, bool]:
            if capped:
                return parts, chars, True
            room = _MAX_STREAM_CHARS - chars
            if room <= 0:
                return parts, chars, True
            if len(text) > room:
                parts.append(text[:room])
                parts.append(_STREAM_TRUNCATION_MARKER)
                return parts, chars + room, True
            parts.append(text)
            return parts, chars + len(text), False

        while True:
            if time.monotonic() - start > timeout:
                timed_out = True
                # Prime's abort path: ask the kernel to interrupt the cell,
                # then give it a bounded grace period to report `done`.
                await self._send({"type": "interrupt", "id": rid})
                message = (
                    f"\n[IPython timed out after {timeout:.0f}s; the cell was interrupted. "
                    "Long work should run as a background `bash()` handle instead.]"
                )
                stdout_parts, stdout_chars, stdout_capped = cap(
                    stdout_parts, stdout_chars, message, stdout_capped
                )
                await stream("__STDOUT__", message)
                deadline = time.monotonic() + _ABORT_GRACE_SECONDS
                while time.monotonic() < deadline:
                    event = await self._next_event(timeout=0.05)
                    if event is None:
                        if self._process is not None and self._process.returncode is not None:
                            break
                        continue
                    if event.get("event") == "done" and event.get("id") == rid:
                        break
                break
            try:
                event = await self._next_event(timeout=0.1)
            except asyncio.CancelledError:
                break
            if event is None:
                if self._process is not None and self._process.returncode is not None:
                    break
                continue
            etype = event.get("event")
            eid = event.get("id")
            # Cell-attributed frames and raw fd frames both belong to the
            # running cell: the kernel only executes one cell at a time.
            if etype in ("stdout", "stderr"):
                text = event.get("text", "")
                if not text:
                    continue
                if etype == "stdout":
                    stdout_parts, stdout_chars, stdout_capped = cap(
                        stdout_parts, stdout_chars, text, stdout_capped
                    )
                    await stream("__STDOUT__", text)
                else:
                    stderr_parts, stderr_chars, stderr_capped = cap(
                        stderr_parts, stderr_chars, text, stderr_capped
                    )
                    await stream("__STDERR__", text)
            # Cells are serialized by ``_execution_lock`` and the kernel tags
            # frames with the *cell* id, so anything that arrives between
            # ``execute`` and ``done`` belongs to the running cell: only
            # ``done`` is matched against the request id.
            elif etype == "result":
                result_text = event.get("text") or ""
                await stream("__RESULT__", result_text)
            elif etype == "display":
                payload = event.get("data")
                display_parts.append(json.dumps(payload, default=str))
                await stream("__DISPLAY__", json.dumps(payload, default=str))
            elif etype == "error":
                errored = True
                ename = event.get("ename", "Error")
                evalue = event.get("evalue", "")
                tb_lines = event.get("traceback") or []
                formatted = f"{ename}: {evalue}"
                if tb_lines:
                    formatted += "\n" + "\n".join(tb_lines)
                error_parts.append(formatted)
                await stream("__ERROR__", formatted)
            elif etype == "host_request":
                await self._handle_host_request(event, self._tool_executor)
            elif etype == "done":
                if eid != rid:
                    continue
                status = event.get("status")
                if status not in (None, "ok"):
                    errored = True
                    if not error_parts:
                        reason = event.get("reason") or status or "error"
                        error_parts.append(f"KernelError: {reason}")
                        await stream("__ERROR__", f"KernelError: {reason}")
                if on_output is not None:
                    await on_output("__DONE__")
                done_event.set()
                break

        # Prime's assembly order: stdout, stderr, result, traceback, then
        # any display payloads the cell emitted but the UI did not render.
        sections = [
            "".join(stdout_parts).strip(),
            "".join(stderr_parts).strip(),
            (result_text or "").strip(),
            "\n".join(error_parts).strip(),
        ]
        output = "\n".join(section for section in sections if section)
        output = truncate_bytes(output, _OUTPUT_TRUNCATE_BYTES)
        if errored:
            return (output, True)
        if timed_out:
            return (output, True)
        if not output:
            if display_parts:
                output = "\n".join(display_parts)
            else:
                # Cell ran cleanly but produced no stdout/result — synthesize
                # a positive confirmation so the model doesn't see an empty
                # tool result and conclude nothing happened.
                output = "(cell executed successfully; no output)"
        return (output, False)

    async def interrupt(self) -> None:
        request = {"type": "interrupt", "id": uuid.uuid4().hex}
        if not await self._send(request):
            pass

    def is_active(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def close(self) -> None:
        self._closed = True
        if self._reader_task:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task
            self._reader_task = None
        self._queue = None
        if self._process and self._process.returncode is None:
            try:
                if self._process.stdin:
                    self._process.stdin.close()
                self._process.kill()
                await self._process.wait()
            except ProcessLookupError:
                pass
        self._process = None

    def touch(self) -> None:
        self._last_activity = time.monotonic()


class KernelPool:
    """Pool of reusable IPython kernels for parallel execution.

    Kernels are created lazily: the subprocess (and therefore its session
    env vars) only comes up on first use, so a pooled kernel can still be
    bound to a session before it spawns.
    """

    def __init__(self, cwd: str, pool_size: int = _DEFAULT_POOL_SIZE) -> None:
        self.cwd = cwd
        self.pool_size = pool_size
        self._kernels: list[IpythonKernel] = []
        self._in_use: dict[str, IpythonKernel] = {}
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        async with self._lock:
            if self._kernels or self._in_use:
                return
            for i in range(self.pool_size):
                self._kernels.append(IpythonKernel(kernel_id=f"pool-{i}", cwd=self.cwd))

    async def acquire(self, session_id: str) -> IpythonKernel:
        """Check out a kernel for the given session."""
        async with self._lock:
            if session_id in self._in_use:
                return self._in_use[session_id]
            if self._kernels:
                kernel = self._kernels.pop()
            else:
                # Pool exhausted: create a temporary kernel
                kernel = IpythonKernel(kernel_id=f"temp-{session_id}", cwd=self.cwd)
            self._in_use[session_id] = kernel
            return kernel

    async def release(self, session_id: str) -> None:
        """Return a kernel to the pool."""
        async with self._lock:
            kernel = self._in_use.pop(session_id, None)
            if kernel is not None and kernel.is_active() and len(self._kernels) < self.pool_size:
                self._kernels.append(kernel)
            elif kernel is not None:
                await kernel.close()

    async def shutdown(self) -> None:
        async with self._lock:
            for kernel in list(self._kernels):
                await kernel.close()
            self._kernels.clear()
            for kernel in list(self._in_use.values()):
                await kernel.close()
            self._in_use.clear()


def session_harness_dir(session_id: str, cwd: str) -> str:
    """Directory handed to the kernel as ``VTX_SESSION_DIR``."""
    from vtx.core.paths import get_config_dir

    safe_cwd = cwd.replace("/", "-").replace("\\", "-").strip("-") or "root"
    return str(get_config_dir() / "sessions" / safe_cwd / session_id)


class IpythonManager:
    """Manages IPython kernels with pooling for parallel subagent execution."""

    def __init__(self, cwd: str, pool_size: int = _DEFAULT_POOL_SIZE) -> None:
        self.cwd = cwd
        self.pool_size = pool_size
        self._pool = KernelPool(cwd, pool_size=pool_size)
        self._session_kernels: dict[str, IpythonKernel] = {}
        self._gc_interval = 60.0
        self._gc_task: asyncio.Task[None] | None = None
        self._background_tasks: set[asyncio.Task[None]] = set()

    async def start(self) -> None:
        await self._pool.start()
        if self._gc_task is None:
            self._gc_task = asyncio.create_task(self._gc_loop())

    async def shutdown(self) -> None:
        if self._gc_task:
            self._gc_task.cancel()
            self._gc_task = None
        await self._pool.shutdown()

    async def execute(
        self,
        session_id: str,
        code: str,
        on_output: Callable[[str], Coroutine[Any, Any, None]] | None = None,
        *,
        timeout: float = 180.0,
        context: dict[str, Any] | None = None,
        tool_executor: Callable[[str, dict[str, Any]], Coroutine[Any, Any, Any]] | None = None,
    ) -> tuple[str, bool]:
        """Run ``code`` in the session kernel.

        Returns ``(output, errored)`` — ``errored`` is ``True`` when the cell
        raised, timed out, or the kernel couldn't start.
        """
        # Use a dedicated kernel for this session if we have one,
        # otherwise check out from the pool.
        if session_id not in self._session_kernels:
            kernel = await self._pool.acquire(session_id)
            self._session_kernels[session_id] = kernel
        else:
            kernel = self._session_kernels[session_id]

        kernel.set_session(session_id, session_harness_dir(session_id, self.cwd))
        kernel.touch()
        try:
            return await kernel.execute(
                code,
                on_output=on_output,
                timeout=timeout,
                context=context,
                tool_executor=tool_executor,
            )
        finally:
            # Return pooled kernels when done, keep dedicated ones.
            if session_id.startswith("pool-") or session_id.startswith("temp-"):
                await self._pool.release(session_id)
                self._session_kernels.pop(session_id, None)

    async def _gc_loop(self) -> None:
        while True:
            await asyncio.sleep(self._gc_interval)
            now = time.monotonic()
            dead = [
                sid
                for sid, kernel in self._session_kernels.items()
                if not kernel.is_active() or now - kernel._last_activity > _MAX_INACTIVITY_SECONDS
            ]
            for sid in dead:
                kernel = self._session_kernels.pop(sid, None)
                if kernel is not None:
                    await kernel.close()
                # Drop the pool checkout too: a closed kernel is terminal, and
                # handing it out again would fail every later execute for it.
                await self._pool.release(sid)

    def dispose(self, session_id: str) -> None:
        kernel = self._session_kernels.pop(session_id, None)
        if kernel is None:
            return

        async def _close_and_release() -> None:
            await kernel.close()
            await self._pool.release(session_id)

        task = asyncio.create_task(_close_and_release())
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)


# Global manager instance
_ipython_manager: IpythonManager | None = None


def get_ipython_manager(cwd: str | None = None) -> IpythonManager:
    """Return the process-wide IPython manager, creating it if needed."""
    global _ipython_manager
    if _ipython_manager is None:
        if cwd is None:
            cwd = os.getcwd()
        _ipython_manager = IpythonManager(cwd)
    return _ipython_manager


# Lazy stdlib shim so tests can patch os.environ without import-order issues.
os_environ: dict[str, str] = {}

"""Persistent IPython REPL kernel manager for RLM mode.

Maintains long-lived ``python -m vtx.ai.agent.ipython_runtime`` subprocesses
keyed by session id. Each subprocess executes snippets sequentially, preserving
imports, variables, and side effects across calls. Communication uses a
newline-delimited JSON protocol inspired by Prime Agent's ``rlm.repl``.

Inspired by JARVIS's kernel pool pattern for parallel subagent execution.
"""

from __future__ import annotations

import asyncio
import codecs
import contextlib
import json
import os
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


class IpythonKernel:
    """One persistent IPython runtime subprocess."""

    def __init__(
        self,
        kernel_id: str,
        cwd: str,
        *,
        python: str | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self.kernel_id = kernel_id
        self.cwd = cwd
        self.python = python or sys.executable
        self._env = env or {}
        self._process: Process | None = None
        self._last_activity = time.monotonic()
        self._execution_lock = asyncio.Lock()
        self._closed = False
        self._reader_task: asyncio.Task[None] | None = None
        self._queue: asyncio.Queue[str | None] | None = None
        self._output_buffer = ""
        self._result_repr: str | None = None
        self._pending_done: dict[str, asyncio.Event] = {}

    async def start(self) -> None:
        if self._process and self._process.returncode is None:
            return
        env = {**dict(os.environ), **self._env}
        self._process = await asyncio.create_subprocess_exec(
            self.python,
            "-m",
            "vtx.ai.agent.ipython_runtime",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.cwd,
            env=env,
        )
        self._last_activity = time.monotonic()
        self._queue = asyncio.Queue()
        self._reader_task = asyncio.create_task(self._read_events())

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
                if self._queue is not None:
                    await self._queue.put(line.strip())
        if self._queue is not None:
            await self._queue.put(None)

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
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    async def execute(
        self,
        code: str,
        on_output: Callable[[str], Coroutine[Any, Any, None]] | None = None,
        *,
        timeout: float = 180.0,
        context: dict[str, Any] | None = None,
    ) -> tuple[str, bool]:
        """Run ``code`` in the kernel.

        Returns ``(output, errored)`` — ``errored`` is ``True`` when the cell
        raised. ``output`` is the streamed stdout/stderr/traceback text (or a
        short status sentence if the cell produced nothing).
        """
        if self._closed:
            return ("REPL kernel is closed. Start a new session.", True)
        await self.start()
        if self._process is None or self._process.stdin is None:
            return ("REPL kernel failed to start.", True)

        async with self._execution_lock:
            self._last_activity = time.monotonic()
            self._output_buffer = ""
            self._result_repr = None

            rid = uuid.uuid4().hex
            done_event = asyncio.Event()
            self._pending_done[rid] = done_event

            request: dict[str, Any] = {"type": "execute", "id": rid, "code": code.rstrip()}
            if context is not None:
                request["context"] = context
            line = json.dumps(request, separators=(",", ":")) + "\n"
            try:
                self._process.stdin.write(line.encode("utf-8"))
                await self._process.stdin.drain()
            except (ConnectionResetError, BrokenPipeError):
                await self.start()
                if self._process.stdin is None:
                    self._pending_done.pop(rid, None)
                    return ("REPL kernel connection was lost and could not be restored.", True)
                self._process.stdin.write(line.encode("utf-8"))
                await self._process.stdin.drain()

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
        while True:
            if time.monotonic() - start > timeout:
                timed_out = True
                if on_output is not None:
                    await on_output(
                        "\n[IPython timed out after "
                        + f"{timeout:.0f}s; the kernel may still be running. "
                        + "Retry or send an empty snippet to flush.]"
                    )
                self._output_buffer += (
                    "\n[IPython timed out after "
                    + f"{timeout:.0f}s; the kernel may still be running. "
                    + "Retry or send an empty snippet to flush.]"
                )
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
            if etype == "stdout":
                text = event.get("text", "")
                self._output_buffer += text
                if on_output is not None and text:
                    await on_output(f"__STDOUT__{text}")
            elif etype == "stderr":
                text = event.get("text", "")
                self._output_buffer += text
                if on_output is not None and text:
                    await on_output(f"__STDERR__{text}")
            elif etype == "result":
                text = event.get("text") or ""
                self._result_repr = text
                if on_output is not None:
                    await on_output(f"__RESULT__{text}")
            elif etype == "error":
                errored = True
                ename = event.get("ename", "Error")
                evalue = event.get("evalue", "")
                tb_lines = event.get("traceback") or []
                formatted = f"{ename}: {evalue}"
                if tb_lines:
                    formatted += "\n" + "\n".join(tb_lines)
                self._output_buffer += f"\n[{ename}] {evalue}\n" + "\n".join(tb_lines)
                if on_output is not None:
                    await on_output(f"__ERROR__{formatted}")
            elif etype == "done":
                if eid == rid:
                    if on_output is not None:
                        await on_output("__DONE__")
                    break
                continue
        output = truncate_bytes(self._output_buffer.strip(), _OUTPUT_TRUNCATE_BYTES)
        if errored:
            return (output, True)
        if timed_out:
            return (output, True)
        if not output:
            if self._result_repr is not None:
                output = self._result_repr
            else:
                # Cell ran cleanly but produced no stdout/result — synthesize
                # a positive confirmation so the model doesn't see an empty
                # tool result and conclude nothing happened.
                output = "(cell executed successfully; no output)"
        return (output, False)

    async def interrupt(self) -> None:
        if self._process and self._process.returncode is None and self._process.stdin is not None:
            request = {"type": "interrupt", "id": uuid.uuid4().hex}
            line = json.dumps(request, separators=(",", ":")) + "\n"
            try:
                self._process.stdin.write(line.encode("utf-8"))
                await self._process.stdin.drain()
            except (ConnectionResetError, BrokenPipeError, ProcessLookupError):
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

    Inspired by JARVIS's ``KernelPool`` for concurrent subagent execution.
    Kernels are checked out by ``session_id`` and returned when done.
    """

    def __init__(self, cwd: str, pool_size: int = _DEFAULT_POOL_SIZE) -> None:
        self.cwd = cwd
        self.pool_size = pool_size
        self._kernels: list[IpythonKernel] = []
        self._in_use: dict[str, IpythonKernel] = {}
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        async with self._lock:
            for i in range(self.pool_size):
                kernel = IpythonKernel(kernel_id=f"pool-{i}", cwd=self.cwd)
                await kernel.start()
                self._kernels.append(kernel)

    async def acquire(self, session_id: str) -> IpythonKernel:
        """Check out a kernel for the given session."""
        async with self._lock:
            if session_id in self._in_use:
                return self._in_use[session_id]
            if self._kernels:
                kernel = self._kernels.pop()
                self._in_use[session_id] = kernel
                return kernel
            # Pool exhausted: create a temporary kernel
            kernel = IpythonKernel(kernel_id=f"temp-{session_id}", cwd=self.cwd)
            await kernel.start()
            self._in_use[session_id] = kernel
            return kernel

    async def release(self, session_id: str) -> None:
        """Return a kernel to the pool."""
        async with self._lock:
            kernel = self._in_use.pop(session_id, None)
            if kernel is not None and kernel.is_active():
                self._kernels.append(kernel)

    async def shutdown(self) -> None:
        async with self._lock:
            for kernel in list(self._kernels):
                await kernel.close()
            self._kernels.clear()
            for kernel in list(self._in_use.values()):
                await kernel.close()
            self._in_use.clear()


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

        kernel.touch()
        try:
            return await kernel.execute(
                code, on_output=on_output, timeout=timeout, context=context
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

    def dispose(self, session_id: str) -> None:
        kernel = self._session_kernels.pop(session_id, None)
        if kernel is not None:
            task = asyncio.create_task(kernel.close())
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

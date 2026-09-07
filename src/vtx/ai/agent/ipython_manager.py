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
import json
import os
import sys
import time
import uuid
from asyncio.subprocess import Process
from collections.abc import Callable, Coroutine
from queue import Empty, Queue
from threading import Thread
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
        self._reader_thread: Thread | None = None
        self._reader_queue: Queue[str] | None = None
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
        self._reader_queue = Queue()
        self._reader_thread = Thread(
            target=self._read_events, args=(self._process.stdout, self._reader_queue), daemon=True
        )
        self._reader_thread.start()

    @staticmethod
    def _read_events(stdout: Any, queue: Queue[str]) -> None:
        """Synchronous reader thread: blocks on stdout and pushes complete lines."""
        assert stdout is not None
        buffer = ""
        decoder = None
        while True:
            try:
                raw = stdout.read(4096)
            except Exception:
                break
            if not raw:
                break
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
                queue.put(line.strip())

    async def _next_event(self, timeout: float) -> dict[str, Any] | None:
        if self._reader_queue is None:
            return None
        try:
            raw = await asyncio.get_event_loop().run_in_executor(
                None, self._reader_queue.get, True, timeout
            )
        except Empty:
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
    ) -> str:
        if self._closed:
            return "REPL kernel is closed. Start a new session."
        await self.start()
        if self._process is None or self._process.stdin is None:
            return "REPL kernel failed to start."

        async with self._execution_lock:
            self._last_activity = time.monotonic()
            self._output_buffer = ""
            self._result_repr = None

            rid = uuid.uuid4().hex
            done_event = asyncio.Event()
            self._pending_done[rid] = done_event

            request = {"type": "execute", "id": rid, "code": code.rstrip()}
            line = json.dumps(request, separators=(",", ":")) + "\n"
            try:
                self._process.stdin.write(line.encode("utf-8"))
                await self._process.stdin.drain()
            except (ConnectionResetError, BrokenPipeError):
                await self.start()
                if self._process.stdin is None:
                    self._pending_done.pop(rid, None)
                    return "REPL kernel connection was lost and could not be restored."
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
    ) -> str:
        start = time.monotonic()
        last_len = 0
        while True:
            if time.monotonic() - start > timeout:
                return (
                    self._output_buffer
                    + "\n[IPython timed out after "
                    + f"{timeout:.0f}s; the kernel may still be running. "
                    + "Retry or send an empty snippet to flush.]"
                )
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
            if etype in ("stdout", "stderr"):
                self._output_buffer += event.get("text", "")
            elif etype == "result":
                self._result_repr = event.get("text")
            elif etype == "error":
                self._output_buffer += f"\n[{event.get('ename')}] {event.get('evalue')}\n"
            elif etype == "done":
                if eid == rid:
                    break
                continue
            if len(self._output_buffer) != last_len:
                last_len = len(self._output_buffer)
                if on_output is not None:
                    await on_output(self._output_buffer[last_len:])
        output = truncate_bytes(self._output_buffer.strip(), _OUTPUT_TRUNCATE_BYTES)
        if self._result_repr is not None and not output:
            output = self._result_repr
        return output

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
        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=1.0)
        self._reader_thread = None
        self._reader_queue = None
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
    ) -> str:
        # Use a dedicated kernel for this session if we have one,
        # otherwise check out from the pool.
        if session_id not in self._session_kernels:
            kernel = await self._pool.acquire(session_id)
            self._session_kernels[session_id] = kernel
        else:
            kernel = self._session_kernels[session_id]

        kernel.touch()
        try:
            return await kernel.execute(code, on_output=on_output, timeout=timeout)
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

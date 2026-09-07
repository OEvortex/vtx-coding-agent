"""Persistent IPython REPL kernel manager for RLM mode.

Maintains long-lived ``python -m vtx.ai.agent.ipython_runtime`` subprocesses
keyed by session id. Each subprocess executes snippets sequentially, preserving
imports, variables, and side effects across calls. Communication uses a
newline-delimited JSON protocol inspired by Prime Agent's ``rlm.repl``.
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
from typing import Any

from vtx.core.bytes_util import truncate_bytes

_MAX_INACTIVITY_SECONDS = 600
_OUTPUT_TRUNCATE_BYTES = 1_048_576  # 1 MiB per tool call


class IpythonKernel:
    """One persistent IPython runtime subprocess."""

    def __init__(
        self,
        session_id: str,
        cwd: str,
        *,
        python: str | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self.session_id = session_id
        self.cwd = cwd
        self.python = python or sys.executable
        self._env = env or {}
        self._process: Process | None = None
        self._last_activity = time.monotonic()
        self._execution_lock = asyncio.Lock()
        self._closed = False
        self._reader_task: asyncio.Task[None] | None = None
        self._output_buffer = ""
        self._result_repr: str | None = None
        self._done_event = asyncio.Event()
        self._pending_requests: dict[str, asyncio.Event] = {}
        self._request_errors: dict[str, str] = {}

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
        self._reader_task = asyncio.create_task(self._read_events())

    async def _read_events(self) -> None:
        assert self._process and self._process.stdout is not None
        buffer = ""
        decoder = None
        while True:
            try:
                raw = await asyncio.wait_for(self._process.stdout.read(4096), timeout=0.5)
            except TimeoutError:
                if self._process.returncode is not None:
                    break
                continue
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
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                await self._handle_event(event)

    async def _handle_event(self, event: dict[str, Any]) -> None:
        etype = event.get("event")
        eid = event.get("id")
        if etype in ("stdout", "stderr"):
            self._output_buffer += event.get("text", "")
            self._done_event.set()
        elif etype == "result":
            self._result_repr = event.get("text")
            self._done_event.set()
        elif etype == "error":
            self._output_buffer += f"\n[{event.get('ename')}] {event.get('evalue')}\n"
            self._done_event.set()
        elif etype == "done":
            if eid is not None and eid in self._pending_requests:
                self._pending_requests.pop(eid).set()
            self._done_event.set()
        elif etype == "ready":
            self._done_event.set()

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
            self._done_event.clear()

            rid = uuid.uuid4().hex
            request = {"type": "execute", "id": rid, "code": code.rstrip()}
            line = json.dumps(request, separators=(",", ":")) + "\n"
            try:
                self._process.stdin.write(line.encode("utf-8"))
                await self._process.stdin.drain()
            except (ConnectionResetError, BrokenPipeError):
                await self.start()
                if self._process.stdin is None:
                    return "REPL kernel connection was lost and could not be restored."
                self._process.stdin.write(line.encode("utf-8"))
                await self._process.stdin.drain()

            return await self._wait_output(rid, on_output=on_output, timeout=timeout)

    async def _wait_output(
        self,
        rid: str,
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
                await asyncio.wait_for(self._done_event.wait(), timeout=0.5)
            except TimeoutError:
                if self._process is not None and self._process.returncode is not None:
                    break
                continue
            self._done_event.clear()
            if len(self._output_buffer) != last_len:
                last_len = len(self._output_buffer)
                if on_output is not None:
                    await on_output(self._output_buffer[last_len:])
            if self._result_repr is not None or (
                self._process is not None and self._process.returncode is not None
            ):
                break
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
        if self._reader_task:
            self._reader_task.cancel()
            self._reader_task = None
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


class IpythonManager:
    """Owns one :class:`IpythonKernel` per session."""

    def __init__(self, cwd: str) -> None:
        self.cwd = cwd
        self._kernels: dict[str, IpythonKernel] = {}
        self._gc_interval = 60.0
        self._gc_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._gc_task is None:
            self._gc_task = asyncio.create_task(self._gc_loop())

    async def shutdown(self) -> None:
        if self._gc_task:
            self._gc_task.cancel()
            self._gc_task = None
        await asyncio.gather(
            *(kernel.close() for kernel in self._kernels.values()), return_exceptions=True
        )
        self._kernels.clear()

    async def execute(
        self,
        session_id: str,
        code: str,
        on_output: Callable[[str], Coroutine[Any, Any, None]] | None = None,
        *,
        timeout: float = 180.0,
    ) -> str:
        if session_id not in self._kernels:
            self._kernels[session_id] = IpythonKernel(session_id, self.cwd)
        kernel = self._kernels[session_id]
        kernel.touch()
        return await kernel.execute(code, on_output=on_output, timeout=timeout)

    async def interrupt(self, session_id: str) -> None:
        kernel = self._kernels.get(session_id)
        if kernel is not None:
            await kernel.interrupt()

    async def _gc_loop(self) -> None:
        while True:
            await asyncio.sleep(self._gc_interval)
            now = time.monotonic()
            dead = [
                sid
                for sid, kernel in self._kernels.items()
                if not kernel.is_active() or now - kernel._last_activity > _MAX_INACTIVITY_SECONDS
            ]
            for sid in dead:
                await self._kernels[sid].close()
                self._kernels.pop(sid, None)

    def dispose(self, session_id: str) -> None:
        self._kernels.pop(session_id, None)


# Lazy stdlib shim so tests can patch os.environ without import-order issues.
os_environ: dict[str, str] = {}

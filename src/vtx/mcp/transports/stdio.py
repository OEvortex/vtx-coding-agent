"""Stdio transport: run an MCP server as a child process, newline-delimited JSON.

Shutdown follows the MCP spec -- close stdin and give the server a moment to
exit on its own, then SIGTERM, then SIGKILL. The child is started in its own
session (``start_new_session=True``) and signalled by process *group*, so a
server spawned through a wrapper like ``npx`` or ``uvx`` does not leave
grandchildren holding the pipe open after the parent is gone.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
import sys
from dataclasses import dataclass, field
from typing import Any

from ..jsonrpc import JsonRpcMessage, McpConnectionClosedError, parse_jsonrpc_message
from ..transport import DEFAULT_MAX_MESSAGE_BYTES, TransportEvents

log = logging.getLogger("mcp.transport.stdio")

DEFAULT_MAX_STDERR_BYTES = 64 * 1024
DEFAULT_CLOSE_TIMEOUT_MS = 2_000
# How long a server gets to exit on its own after stdin closes, before SIGTERM.
STDIN_CLOSE_GRACE_MS = 500

_IS_WINDOWS = sys.platform == "win32"
# Windows has no process groups and no graceful signals.
USE_PROCESS_GROUPS = not _IS_WINDOWS

# Servers this process started, so a crash of the host does not orphan them.
_live_processes: set[int] = set()
_exit_hook_installed = False


def _install_exit_hook() -> None:
    global _exit_hook_installed
    if _exit_hook_installed:
        return
    _exit_hook_installed = True

    def _kill_live() -> None:
        for pid in list(_live_processes):
            with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
                os.killpg(pid, signal.SIGTERM)

    import atexit

    atexit.register(_kill_live)


def _kill_process_tree(process: asyncio.subprocess.Process, sig: int) -> None:
    pid = process.pid
    if pid is None or process.returncode is not None:
        return
    if _IS_WINDOWS:
        # No graceful signals on Windows, and a ``.cmd`` shim runs under
        # cmd.exe, so killing only the direct child leaves the server running.
        with contextlib.suppress(OSError):
            subprocess = asyncio.create_subprocess_exec(  # noqa: S603
                "taskkill",
                "/pid",
                str(pid),
                "/T",
                "/F",
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            with contextlib.suppress(Exception):
                asyncio.ensure_future(subprocess)
        return
    if USE_PROCESS_GROUPS:
        try:
            # Negative pid addresses the whole group, so wrappers do not leave
            # the server behind. start_new_session=True made the child a group
            # leader, so its pgid equals its pid.
            os.killpg(os.getpgid(pid), sig)
            return
        except (ProcessLookupError, PermissionError, OSError):
            # The group is gone or was never created; fall through to the child.
            pass
    with contextlib.suppress(ProcessLookupError, OSError):
        process.send_signal(sig)


@dataclass
class StdioTransportOptions:
    command: str
    args: list[str] = field(default_factory=list)
    cwd: str | None = None
    env: dict[str, str] | None = None
    inherit_env: bool = True
    """``False`` gives the child only ``env``, not the parent environment."""
    stderr: str = "pipe"
    """``"inherit"`` sends the server's stderr to vtx's own stderr."""
    on_stderr: Any = None
    max_message_bytes: int = DEFAULT_MAX_MESSAGE_BYTES
    max_stderr_bytes: int = DEFAULT_MAX_STDERR_BYTES
    close_timeout_ms: int = DEFAULT_CLOSE_TIMEOUT_MS
    """Time between SIGTERM and SIGKILL during shutdown."""


class StdioTransport(TransportEvents):
    def __init__(self, options: StdioTransportOptions) -> None:
        super().__init__()
        self.options = options
        self._process: asyncio.subprocess.Process | None = None
        self._stdout_buffer = bytearray()
        self._stderr_buffer = bytearray()
        self._started = False
        self._closed = False
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._waiter_task: asyncio.Task | None = None

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process is not None else None

    @property
    def stderr(self) -> str:
        """The tail of the server's stderr, for diagnosing a failed connect."""
        return self._stderr_buffer.decode("utf-8", "replace")

    async def start(self) -> None:
        if self._started:
            raise RuntimeError("MCP stdio transport already started")
        if self._closed:
            raise McpConnectionClosedError()
        self._started = True

        env: dict[str, str] | None = None
        if not self.options.inherit_env:
            env = dict(self.options.env or {})
        elif self.options.env:
            env = {**os.environ, **self.options.env}

        try:
            process = await asyncio.create_subprocess_exec(
                self.options.command,
                *self.options.args,
                cwd=self.options.cwd,
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                # "inherit" passes None, which is asyncio's "do not redirect".
                stderr=None if self.options.stderr == "inherit" else asyncio.subprocess.PIPE,
                # Own session, so closing the transport can terminate the
                # server's children too.
                start_new_session=USE_PROCESS_GROUPS,
            )
        except OSError as exc:
            self._started = False
            raise RuntimeError(
                f"Failed to start MCP server {self.options.command!r}: {exc}"
            ) from exc

        self._process = process
        if USE_PROCESS_GROUPS and process.pid is not None:
            _install_exit_hook()
            _live_processes.add(process.pid)

        if process.stdout is not None:
            self._reader_task = asyncio.ensure_future(self._read_stdout(process.stdout))
        if process.stderr is not None:
            self._stderr_task = asyncio.ensure_future(self._read_stderr(process.stderr))
        self._waiter_task = asyncio.ensure_future(self._wait_for_exit(process))

    async def send(self, message: JsonRpcMessage) -> None:
        process = self._process
        if not self._started or self._closed or process is None or process.stdin is None:
            raise McpConnectionClosedError()
        if process.stdin.is_closing():
            raise McpConnectionClosedError()
        try:
            process.stdin.write(_encode_message(message))
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError, RuntimeError) as exc:
            raise McpConnectionClosedError(f"MCP server stdin closed: {exc}") from exc

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        process = self._process
        if process is None:
            self.emit_close()
            return

        if process.returncode is not None:
            # Already dead; just release the pipe.
            with contextlib.suppress(Exception):
                if process.stdin is not None:
                    process.stdin.close()
            self.emit_close()
            return

        # Spec shutdown: close stdin, let the server exit, then SIGTERM, then
        # SIGKILL. The extra SIGTERM after exit is deliberate: children of the
        # server that ignored stdin closing would otherwise outlive it.
        close_timeout = self.options.close_timeout_ms / 1000
        grace = min(STDIN_CLOSE_GRACE_MS / 1000, close_timeout)
        with contextlib.suppress(Exception):
            if process.stdin is not None:
                process.stdin.close()

        waiter = self._waiter_task
        try:
            if waiter is not None:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(asyncio.shield(waiter), timeout=grace)
                if waiter.done():
                    _kill_process_tree(process, signal.SIGTERM)
                    return
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(asyncio.shield(waiter), timeout=close_timeout)
                if waiter.done():
                    _kill_process_tree(process, signal.SIGTERM)
                    return
            _kill_process_tree(process, signal.SIGTERM)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(asyncio.shield(waiter or _noop()), timeout=close_timeout)
            _kill_process_tree(process, signal.SIGKILL)
        finally:
            self._cancel_task(self._reader_task)
            self._cancel_task(self._stderr_task)
            _live_processes.discard(process.pid or -1)
            self.emit_close()

    # ---- io loops --------------------------------------------------------

    async def _read_stdout(self, stream: asyncio.StreamReader) -> None:
        max_bytes = self.options.max_message_bytes
        try:
            while True:
                chunk = await stream.read(65536)
                if not chunk:
                    break
                self._stdout_buffer.extend(chunk)
                while True:
                    newline = self._stdout_buffer.find(b"\n")
                    if newline < 0:
                        if len(self._stdout_buffer) > max_bytes:
                            self._stdout_buffer.clear()
                            self.emit_error(
                                RuntimeError(f"MCP stdio message exceeds {max_bytes} bytes")
                            )
                        break
                    line = bytes(self._stdout_buffer[:newline])
                    del self._stdout_buffer[: newline + 1]
                    if len(line) > max_bytes:
                        self.emit_error(
                            RuntimeError(f"MCP stdio message exceeds {max_bytes} bytes")
                        )
                        continue
                    text = line.decode("utf-8", "replace").rstrip("\r")
                    if not text.strip():
                        continue
                    try:
                        self.emit_message(parse_jsonrpc_message(json.loads(text)))
                    except Exception as exc:
                        # A server that writes a stray log line to stdout is a
                        # real failure mode; report it, keep the connection.
                        self.emit_error(exc)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not self._closed:
                self.emit_error(exc)

    async def _read_stderr(self, stream: asyncio.StreamReader) -> None:
        max_bytes = self.options.max_stderr_bytes
        try:
            while True:
                chunk = await stream.read(65536)
                if not chunk:
                    break
                self._stderr_buffer.extend(chunk)
                if len(self._stderr_buffer) > max_bytes:
                    del self._stderr_buffer[:-max_bytes]
                if self.options.on_stderr is not None:
                    with contextlib.suppress(Exception):
                        self.options.on_stderr(chunk.decode("utf-8", "replace"))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not self._closed:
                self.emit_error(exc)

    async def _wait_for_exit(self, process: asyncio.subprocess.Process) -> None:
        try:
            await process.wait()
        except asyncio.CancelledError:
            raise
        except Exception:
            return
        self._process = None
        if self._stdout_buffer.strip():
            # A partial line means the server died mid-message; the pending
            # request will fail on close, and this says why.
            self.emit_error(
                RuntimeError("MCP stdio server closed with an incomplete JSON-RPC message")
            )
        self._stdout_buffer.clear()
        self.emit_close()

    @staticmethod
    def _cancel_task(task: asyncio.Task | None) -> None:
        if task is not None and not task.done():
            task.cancel()


async def _noop() -> None:
    return None


def _encode_message(message: JsonRpcMessage) -> bytes:
    return (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")


__all__ = [
    "DEFAULT_CLOSE_TIMEOUT_MS",
    "DEFAULT_MAX_STDERR_BYTES",
    "STDIN_CLOSE_GRACE_MS",
    "StdioTransport",
    "StdioTransportOptions",
]

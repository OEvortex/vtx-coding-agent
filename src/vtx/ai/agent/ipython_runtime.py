"""Minimal IPython REPL runtime speaking newline-delimited JSON over stdio.

Inspired by Prime Agent's ``rlm.repl`` protocol. Entry point:
``python -m vtx.ai.agent.ipython_runtime``.

Cells execute with top-level await in one persistent ``__main__`` namespace
on a single asyncio event loop. The wire format is newline-delimited JSON:
one object per line, UTF-8, no other framing.
"""

from __future__ import annotations

import asyncio
import codecs
import contextlib
import io
import json
import os
import platform
import signal
import sys
import threading
import traceback
import uuid
from collections.abc import Coroutine
from typing import Any

PROTOCOL_VERSION = 1

_write_lock = threading.Lock()
_loop: asyncio.AbstractEventLoop | None = None
_protocol_fd: int = -1
_current_cell: str | None = None
_cell_counter = 0
_namespace: dict[str, Any] = {}
_shutdown = False


def _send(event: dict[str, Any]) -> None:
    data = (json.dumps(event, separators=(",", ":")) + "\n").encode("utf-8")
    with _write_lock, contextlib.suppress(OSError):
        os.write(_protocol_fd, data)


def _run_cell(code: str) -> tuple[str, str | None]:
    """Execute *code* in ``_namespace`` and return (stdout, result_repr)."""
    global _current_cell
    cell_id = uuid.uuid4().hex
    _current_cell = cell_id

    old_stdout = sys.stdout
    old_stderr = sys.stderr
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    sys.stdout = _CellStdout(stdout_buf, cell_id)
    sys.stderr = _CellStderr(stderr_buf, cell_id)

    result_repr = None
    try:
        # Top-level await support.
        if "await " in code or code.strip().startswith("async "):
            coro: Coroutine[Any, Any, Any] | None = eval(  # type: ignore[call-overload]
                compile(code, f"<cell-{cell_id}>", "eval"), _namespace
            )
            if asyncio.iscoroutine(coro):
                loop = asyncio.get_event_loop()
                result = loop.run_until_complete(coro)
                if result is not None:
                    result_repr = repr(result)
                sys.stdout.write(repr(result) + "\n")
        else:
            compiled = compile(code, f"<cell-{cell_id}>", "exec")
            exec(compiled, _namespace)  # type: ignore[arg-type]
            # Trailing expression?
            try:
                expr = compile(code, f"<cell-{cell_id}>", "eval")
            except SyntaxError:
                pass
            else:
                try:
                    value = eval(expr, _namespace)  # type: ignore[call-overload]
                except Exception:
                    pass
                else:
                    if value is not None:
                        result_repr = repr(value)
                        sys.stdout.write(repr(value) + "\n")
    except Exception:
        tb = traceback.format_exc()
        sys.stderr.write(tb)
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr
        _current_cell = None

    return stdout_buf.getvalue(), result_repr


class _CellStdout:
    def __init__(self, buf: io.StringIO, cell_id: str) -> None:
        self._buf = buf
        self._cell_id = cell_id

    def write(self, text: str) -> None:
        self._buf.write(text)
        _send({"event": "stdout", "id": self._cell_id, "text": text})

    def flush(self) -> None:
        pass


class _CellStderr:
    def __init__(self, buf: io.StringIO, cell_id: str) -> None:
        self._buf = buf
        self._cell_id = cell_id

    def write(self, text: str) -> None:
        self._buf.write(text)
        _send({"event": "stderr", "id": self._cell_id, "text": text})

    def flush(self) -> None:
        pass


async def _handle_execute(request: dict[str, Any]) -> None:
    global _shutdown
    rid = request.get("id", uuid.uuid4().hex)
    code = request.get("code", "")
    try:
        _stdout_text, result_repr = _run_cell(code)
        if result_repr is not None:
            _send({"event": "result", "id": rid, "text": result_repr})
        _send({"event": "done", "id": rid, "status": "ok"})
    except Exception as exc:
        tb = traceback.format_exc()
        _send(
            {
                "event": "error",
                "id": rid,
                "ename": type(exc).__name__,
                "evalue": str(exc),
                "traceback": tb.splitlines(),
            }
        )
        _send({"event": "done", "id": rid, "status": "error"})


async def _handle_interrupt(request: dict[str, Any]) -> None:
    os.kill(os.getpid(), signal.SIGINT)


async def _handle_shutdown(request: dict[str, Any]) -> None:
    global _shutdown
    _shutdown = True
    rid = request.get("id")
    if rid is not None:
        _send({"event": "done", "id": rid, "status": "ok"})


async def _serve() -> None:
    global _shutdown
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    while not _shutdown:
        try:
            raw = await asyncio.get_event_loop().run_in_executor(None, sys.stdin.buffer.read, 4096)
        except Exception:
            break
        if not raw:
            break
        text = decoder.decode(raw)
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                _send(
                    {
                        "event": "error",
                        "id": None,
                        "ename": "ProtocolError",
                        "evalue": "Invalid JSON",
                        "traceback": [],
                    }
                )
                continue
            rtype = request.get("type")
            if rtype == "execute":
                await _handle_execute(request)
            elif rtype == "interrupt":
                await _handle_interrupt(request)
            elif rtype == "shutdown":
                await _handle_shutdown(request)
            else:
                _send(
                    {
                        "event": "error",
                        "id": None,
                        "ename": "ProtocolError",
                        "evalue": f"Unknown request type: {rtype}",
                        "traceback": [],
                    }
                )


def main() -> None:
    global _loop, _protocol_fd
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)
    _protocol_fd = sys.stdout.fileno()
    _send({"event": "ready", "protocol": PROTOCOL_VERSION, "python": platform.python_version()})
    with contextlib.suppress(KeyboardInterrupt):
        _loop.run_until_complete(_serve())


if __name__ == "__main__":
    main()

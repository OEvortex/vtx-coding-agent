"""Minimal IPython REPL runtime speaking newline-delimited JSON over stdio.

Inspired by Prime Agent's ``rlm.repl`` protocol. Entry point:
``python -m vtx.ai.agent.ipython_runtime``.

Cells execute in a worker thread; stdout/stderr are captured and sent
synchronously from the thread. This keeps the protocol simple and avoids
the event-loop blocking problem.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import json
import os
import platform
import signal
import sys
import threading
import traceback
import uuid
from typing import Any

PROTOCOL_VERSION = 1

_write_lock = threading.Lock()
_protocol_fd: int = -1
_namespace: dict[str, Any] = {}
_shutdown = False
_ready_event = threading.Event()


def _send(event: dict[str, Any]) -> None:
    data = (json.dumps(event, separators=(",", ":")) + "\n").encode("utf-8")
    with _write_lock, contextlib.suppress(OSError):
        os.write(_protocol_fd, data)


def _run_cell_sync(code: str, cell_id: str) -> None:
    """Run a cell synchronously in the worker thread; send events directly."""
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    sys.stdout = _CellStdout(cell_id)
    sys.stderr = _CellStderr(cell_id)

    try:
        compiled = compile(code, f"<cell-{cell_id}>", "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
        exec(compiled, _namespace)
        # If the last statement is a bare expression, evaluate it and emit
        # its repr as a result. This matches IPython/REPL behavior.
        try:
            tree = ast.parse(code, mode="exec")
        except SyntaxError:
            tree = None
        if tree is not None and tree.body:
            last = tree.body[-1]
            if isinstance(last, ast.Expr):
                expr_ast = last.value
                expr_code = compile(ast.Expression(expr_ast), f"<cell-{cell_id}>", "eval")
                try:
                    value = eval(expr_code, _namespace)
                except Exception:
                    pass
                else:
                    if value is not None:
                        sys.stdout.write(repr(value) + "\n")
    except Exception:
        tb = traceback.format_exc()
        sys.stderr.write(tb)
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr


class _CellStdout:
    def __init__(self, cell_id: str) -> None:
        self._cell_id = cell_id

    def write(self, text: str) -> None:
        _send({"event": "stdout", "id": self._cell_id, "text": text})

    def flush(self) -> None:
        pass


class _CellStderr:
    def __init__(self, cell_id: str) -> None:
        self._cell_id = cell_id

    def write(self, text: str) -> None:
        _send({"event": "stderr", "id": self._cell_id, "text": text})

    def flush(self) -> None:
        pass


def _execute_in_thread(request: dict[str, Any]) -> None:
    rid = request.get("id", uuid.uuid4().hex)
    code = request.get("code", "")
    cell_id = uuid.uuid4().hex
    try:
        _run_cell_sync(code, cell_id)
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


def main() -> None:
    global _protocol_fd
    _protocol_fd = sys.stdout.fileno()
    _send({"event": "ready", "protocol": PROTOCOL_VERSION, "python": platform.python_version()})
    _ready_event.set()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def serve() -> None:
        global _shutdown
        while not _shutdown:
            try:
                raw = await loop.run_in_executor(None, sys.stdin.buffer.readline)
            except Exception:
                break
            if not raw:
                break
            line = raw.decode("utf-8", "replace").strip()
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
                # Dispatch to a worker thread so the asyncio loop stays free
                # to keep reading stdin.
                await loop.run_in_executor(None, _execute_in_thread, request)
            elif rtype == "interrupt":
                os.kill(os.getpid(), signal.SIGINT)
            elif rtype == "shutdown":
                _shutdown = True
                rid = request.get("id")
                if rid is not None:
                    _send({"event": "done", "id": rid, "status": "ok"})
                _send(
                    {
                        "event": "error",
                        "id": None,
                        "ename": "ProtocolError",
                        "evalue": f"Unknown request type: {rtype}",
                        "traceback": [],
                    }
                )

    with contextlib.suppress(KeyboardInterrupt):
        loop.run_until_complete(serve())


if __name__ == "__main__":
    main()

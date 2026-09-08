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
import inspect
import json
import os
import platform
import re
import signal
import sys
import threading
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any

PROTOCOL_VERSION = 1

_write_lock = threading.Lock()
_protocol_fd: int = -1
_namespace: dict[str, Any] = {}
_shutdown = False
_ready_event = threading.Event()

# IPython-style execution history: In[n] has input code string, Out[n] has returned repr/value.
# _In holds the list of input codes (1-indexed, In[0] is empty string).
# _Out holds the mapping of cell execution numbers to evaluated results.
In: list[str] = [""]
Out: dict[int, Any] = {}
_namespace["In"] = In
_namespace["Out"] = Out
_namespace["_ih"] = In
_namespace["_oh"] = Out
_cell_number = 0


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


def _init_builtin_helpers() -> None:
    """Initialize pre-bound helpers in the REPL namespace if not already present."""

    def run_bash(command: str, timeout: float = 180.0) -> str:
        import subprocess

        res = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=timeout)
        out = res.stdout or ""
        if res.stderr:
            out += ("\n" if out else "") + res.stderr
        return out.strip()

    def read_file(path: str, offset: int = 0, limit: int = 2000) -> str:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return "".join(lines[offset : offset + limit])

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
        """Dynamically execute code in the REPL namespace and return its last expression value."""
        flags = ast.PyCF_ALLOW_TOP_LEVEL_AWAIT
        tree = None
        trailing = None
        with contextlib.suppress(SyntaxError):
            tree = ast.parse(code_str, mode="exec")
        if tree is not None and tree.body and isinstance(tree.body[-1], ast.Expr):
            last_expr = tree.body.pop()
            trailing = ast.Expression(last_expr.value)

        val = None
        if tree is not None and tree.body:
            c = compile(tree, "<dynamic-cell>", "exec", flags=flags)
            res = eval(c, _namespace)
            if inspect.iscoroutine(res):
                val = asyncio.run(res)
        elif tree is None:
            c = compile(code_str, "<dynamic-cell>", "exec", flags=flags)
            res = eval(c, _namespace)
            if inspect.iscoroutine(res):
                val = asyncio.run(res)

        if trailing is not None:
            c_expr = compile(trailing, "<dynamic-cell>", "eval", flags=flags)
            val = eval(c_expr, _namespace)
            if inspect.iscoroutine(val):
                val = asyncio.run(val)
            if val is not None:
                _namespace["_"] = val
        return val

    def rerun(index: int = -1) -> Any:
        """Re-run a previously executed cell or code snippet by index (default: last)."""
        ctx: RLMContext | None = _namespace.get("context")
        code = ctx.get_code(index) if ctx else ""
        if not code and len(In) > 1:
            code = In[index]
        if not code:
            raise ValueError(f"No previous code found at index {index}")
        return run_code(code)

    _namespace.setdefault("bash", run_bash)
    _namespace.setdefault("run_bash", run_bash)
    _namespace.setdefault("read_file", read_file)
    _namespace.setdefault("write_file", write_file)
    _namespace.setdefault("edit_file", edit_file)
    _namespace.setdefault("run_code", run_code)
    _namespace.setdefault("rerun", rerun)


_init_builtin_helpers()


def _send(event: dict[str, Any]) -> None:
    data = (json.dumps(event, separators=(",", ":")) + "\n").encode("utf-8")
    with _write_lock, contextlib.suppress(OSError):
        os.write(_protocol_fd, data)


def _run_cell_sync(code: str, cell_id: str) -> None:
    """Run a cell synchronously in the worker thread; send events directly."""
    global _cell_number
    _cell_number += 1
    cell_num = _cell_number

    # Maintain IPython-style In history: In[n] = code string
    In.append(code)
    _namespace["In"] = In
    _namespace["_ih"] = In
    _namespace["_i"] = code
    if len(In) > 2:
        _namespace["_ii"] = In[-2]
    if len(In) > 3:
        _namespace["_iii"] = In[-3]

    old_stdout = sys.stdout
    old_stderr = sys.stderr
    sys.stdout = _CellStdout(cell_id)
    sys.stderr = _CellStderr(cell_id)

    try:
        # Separate trailing expression if present so bare expressions emit repr like IPython
        tree = None
        trailing_expr = None
        with contextlib.suppress(SyntaxError):
            tree = ast.parse(code, mode="exec")

        if tree is not None and tree.body and isinstance(tree.body[-1], ast.Expr):
            # If the last statement is an Expr, compile body without it
            # and compile the trailing expr separately in eval mode.
            trailing_node = tree.body.pop()
            assert isinstance(trailing_node, ast.Expr)
            trailing_expr = ast.Expression(trailing_node.value)

        flags = ast.PyCF_ALLOW_TOP_LEVEL_AWAIT
        if tree is not None and tree.body:
            compiled_body = compile(tree, f"<cell-{cell_id}>", "exec", flags=flags)
            res = eval(compiled_body, _namespace)
            if inspect.iscoroutine(res):
                asyncio.run(res)
        elif tree is None:
            compiled = compile(code, f"<cell-{cell_id}>", "exec", flags=flags)
            res = eval(compiled, _namespace)
            if inspect.iscoroutine(res):
                asyncio.run(res)

        if trailing_expr is not None:
            compiled_expr = compile(trailing_expr, f"<cell-{cell_id}>", "eval", flags=flags)
            value = eval(compiled_expr, _namespace)
            if inspect.iscoroutine(value):
                value = asyncio.run(value)
            if value is not None:
                # Update _, __, ___ and Out[cell_num]
                if "_" in _namespace:
                    _namespace["___"] = _namespace.get("__")
                    _namespace["__"] = _namespace["_"]
                _namespace["_"] = value
                Out[cell_num] = value
                _namespace["Out"] = Out
                _namespace["_oh"] = Out
                sys.stdout.write(repr(value) + "\n")
    except Exception:
        tb = traceback.format_exc()
        sys.stderr.write(tb)
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr


def _has_call(node: ast.AST) -> bool:
    """Return True when ``node`` (or any descendant) is a ``Call`` expression."""
    return any(isinstance(child, ast.Call) for child in ast.walk(node))


def _has_yield(node: ast.AST) -> bool:
    """Return True when ``node`` (or any descendant) is a ``Yield`` expression."""
    return any(isinstance(child, (ast.Yield, ast.YieldFrom)) for child in ast.walk(node))


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
    ctx_dict = request.get("context")
    if ctx_dict is not None or "context" not in _namespace:
        _update_context_in_namespace(ctx_dict)
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
                break
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

    with contextlib.suppress(KeyboardInterrupt):
        loop.run_until_complete(serve())


if __name__ == "__main__":
    main()

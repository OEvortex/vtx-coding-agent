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
import importlib
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
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = 1

_write_lock = threading.Lock()
_state_lock = threading.Lock()
_protocol_fd: int = -1
_namespace: dict[str, Any] = {}
_shutdown = False
_ready_event = threading.Event()
_MAX_OUT_ENTRIES = 1000  # Cap In/Out history to prevent unbounded memory growth

# Tool-call RPC bridge state (main process -> worker thread)
_tool_call_responses: dict[str, Any] = {}
_tool_call_events: dict[str, threading.Event] = {}

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
    _init_python_skills(ctx_dict.get("cwd"))


def _init_builtin_helpers() -> None:
    """Initialize pre-bound helpers in the REPL namespace if not already present."""

    class BashHandle:
        """Non-blocking handle for a background shell command.

        Created by ``bash(cmd, background=True)``. The process runs
        concurrently; poll or await it without blocking the kernel.
        """

        def __init__(self, command: str, proc: Any) -> None:
            self.command = command
            self._proc = proc
            self.pid = proc.pid
            self._chunks: list[str] = []
            self._done = threading.Event()
            self._lock = threading.Lock()

        def _append(self, text: str) -> None:
            with self._lock:
                self._chunks.append(text)

        def _combined(self) -> str:
            with self._lock:
                return "".join(self._chunks)

        @property
        def running(self) -> bool:
            return self._proc.poll() is None

        def poll(self) -> dict[str, Any] | None:
            """Non-blocking status check. Returns None while running."""
            rc = self._proc.poll()
            if rc is None:
                return None
            self._done.set()
            return {"exit_code": rc, "output": self._combined()}

        def tail(self, n: int = 50) -> str:
            """Last ``n`` lines of combined stdout+stderr so far."""
            lines = self._combined().splitlines()
            return "\n".join(lines[-n:])

        def output(self) -> str:
            """All combined stdout+stderr captured so far."""
            return self._combined()

        def kill(self) -> None:
            """Terminate the process (SIGTERM, escalating to SIGKILL)."""
            with contextlib.suppress(Exception):
                self._proc.terminate()
            if not self._done.wait(timeout=5):
                with contextlib.suppress(Exception):
                    self._proc.kill()
            self._done.set()

        def wait(self, timeout: float | None = None) -> dict[str, Any]:
            """Block until completion; return exit_code/output/duration."""
            import time as _time

            start = _time.monotonic()
            with contextlib.suppress(Exception):
                self._proc.wait(timeout=timeout)
            self._done.set()
            rc = self._proc.poll()
            return {
                "exit_code": rc,
                "output": self._combined(),
                "duration": _time.monotonic() - start,
            }

        def __await__(self) -> Any:
            async def _wait_async() -> dict[str, Any]:
                loop = asyncio.get_running_loop()
                rc = await loop.run_in_executor(None, self._proc.wait)
                self._done.set()
                return {"exit_code": rc, "output": self._combined()}

            return _wait_async().__await__()

        def __repr__(self) -> str:
            state = "running" if self.running else "done"
            return f"<bash pid={self.pid} {state} cmd={self.command[:60]!r}>"

    def _spawn_background(command: str) -> BashHandle:
        import subprocess as _sp

        proc = _sp.Popen(
            command, shell=True, stdout=_sp.PIPE, stderr=_sp.STDOUT, text=True, bufsize=1
        )
        handle = BashHandle(command, proc)

        def _pump() -> None:
            try:
                assert proc.stdout is not None
                for line in proc.stdout:
                    handle._append(line)
            except Exception:
                pass
            finally:
                handle._done.set()
                with contextlib.suppress(Exception):
                    if proc.stdout is not None:
                        proc.stdout.close()

        threading.Thread(target=_pump, name="bash-bg-pump", daemon=True).start()
        return handle

    def bash(command: str, timeout: float = 180.0, background: bool = False) -> Any:
        """Run a shell command.

        Blocking by default: waits up to ``timeout`` seconds and returns
        combined stdout+stderr as a string. Pass ``background=True`` to
        return a :class:`BashHandle` immediately for long-running work;
        then use ``h.running`` / ``h.tail(n)`` / ``h.poll()`` /
        ``h.kill()`` / ``await h``.
        """
        if background:
            return _spawn_background(command)
        import subprocess

        res = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=timeout)
        out = res.stdout or ""
        if res.stderr:
            out += ("\n" if out else "") + res.stderr
        return out.strip()

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
            res = eval(c, _namespace)  # eval() handles top-level await in CPython
            if inspect.iscoroutine(res):
                val = asyncio.run(res)
        elif tree is not None:
            # Empty or whitespace-only cell after stripping trailing expression
            pass
        # Removed dead 'elif tree is None' branch: if ast.parse fails, compile also fails

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

    def rlm(
        description: str,
        prompt: str | None = None,
        subagent_type: str = "general-purpose",
        model: str | None = None,
        background: bool = False,
        **kwargs: Any,
    ) -> str:
        """Spawn a subagent via the main-process task tool.

        Accepts both ``rlm(description, prompt)`` and the single-argument
        shorthand ``rlm(prompt)``. Foreground (default) blocks until the
        child finishes and returns its final answer text. With
        ``background=True`` it returns a task id immediately and the
        result arrives next turn. Unknown keywords (e.g. ``name``,
        ``thinking``) are ignored for forward compatibility.
        """
        _ = kwargs.pop("name", None)
        _ = kwargs.pop("thinking", None)
        if kwargs:
            raise TypeError(f"rlm() got unexpected keyword arguments: {sorted(kwargs)}")
        if prompt is None:
            # Single-argument shorthand: rlm("do X") -> description + prompt.
            prompt = description
            words = description.split()
            description = " ".join(words[:5]) or "Sub-agent task"
        args: dict[str, Any] = {
            "description": description[:128],
            "prompt": prompt,
            "subagent_type": subagent_type,
            "background": background,
        }
        if model is not None:
            args["model"] = model
        return call_tool("task", **args)

    _namespace.setdefault("bash", run_bash)
    _namespace.setdefault("run_bash", run_bash)
    _namespace.setdefault("read_file", read_file)
    _namespace.setdefault("write_file", write_file)
    _namespace.setdefault("edit_file", edit_file)
    _namespace.setdefault("run_code", run_code)
    _namespace.setdefault("rerun", rerun)
    _namespace.setdefault("web_search", web_search)
    _namespace.setdefault("goal_get", goal_get)
    _namespace.setdefault("goal_update", goal_update)
    _namespace.setdefault("goal_set_tasks", goal_set_tasks)
    _namespace.setdefault("rlm", rlm)
    _namespace.setdefault("call_tool", call_tool)


def _init_python_skills(cwd: str | None = None) -> None:
    """Discover Python-backed skills and bind their callable modules in the REPL namespace.

    Follows the Prime Agent Python skills protocol:
    - Finds skills with pyproject.toml and src/<import_name>/__init__.py
    - Appends src/ to sys.path
    - Imports and wraps each module via `vtx.skill.wrap_skill_module`
    - Binds the import name in `_namespace`
    """
    from vtx.skill import FailedSkillModule, wrap_skill_module

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


def call_tool(name: str, **kwargs: Any) -> Any:
    """Call a main-process tool from the REPL via the JSON tool-call RPC.

    Sends a ``tool_call`` event to the manager and blocks until the manager
    writes a ``tool_result`` request back through stdin. Raises on tool error
    or timeout.
    """
    rid = uuid.uuid4().hex
    event = threading.Event()
    _tool_call_events[rid] = event
    _send({"event": "tool_call", "id": rid, "name": name, "args": kwargs})
    if not event.wait(timeout=300):
        _tool_call_events.pop(rid, None)
        raise TimeoutError(f"Tool call {name} timed out after 300s")
    result = _tool_call_responses.pop(rid, None)
    _tool_call_events.pop(rid, None)
    if isinstance(result, Exception):
        raise result
    return result


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


_init_builtin_helpers()
_init_python_skills()


def _send(event: dict[str, Any]) -> None:
    data = (json.dumps(event, separators=(",", ":")) + "\n").encode("utf-8")
    with _write_lock, contextlib.suppress(OSError):
        os.write(_protocol_fd, data)


def _run_cell_sync(code: str, cell_id: str) -> None:
    """Run a cell synchronously in the worker thread; send events directly."""
    code = transform_cell_code(code)
    global _cell_number
    with _state_lock:
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
            res = eval(compiled_body, _namespace)  # eval() handles top-level await in CPython
            if inspect.iscoroutine(res):
                asyncio.run(res)
        # Removed dead 'elif tree is None' branch: if ast.parse fails, compile also fails

        if trailing_expr is not None:
            compiled_expr = compile(trailing_expr, f"<cell-{cell_id}>", "eval", flags=flags)
            value = eval(compiled_expr, _namespace)
            if inspect.iscoroutine(value):
                value = asyncio.run(value)
            if value is not None:
                with _state_lock:
                    # Update _, __, ___ and Out[cell_num]
                    if "_" in _namespace:
                        _namespace["___"] = _namespace.get("__")
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
                sys.stdout.write(repr(value) + "\n")
    except Exception:
        tb = traceback.format_exc()
        sys.stderr.write(tb)
        raise
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

    line_queue: asyncio.Queue[str | None] = asyncio.Queue()

    def stdin_reader() -> None:
        while True:
            try:
                line = sys.stdin.readline()
            except Exception:
                break
            if not line:
                break
            loop.call_soon_threadsafe(line_queue.put_nowait, line)
        loop.call_soon_threadsafe(line_queue.put_nowait, None)

    reader_thread = threading.Thread(target=stdin_reader, name="ipython-stdin-reader", daemon=True)
    reader_thread.start()

    async def serve() -> None:
        global _shutdown
        while not _shutdown:
            try:
                raw_line = await line_queue.get()
            except Exception:
                break
            if raw_line is None:
                break
            line = raw_line.strip()
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
            # Manager -> worker tool-result responses use `event`, not `type`.
            if request.get("event") == "tool_result":
                rid = request.get("id")
                if rid in _tool_call_events:
                    if "error" in request:
                        _tool_call_responses[rid] = RuntimeError(
                            request["error"].get("message", str(request["error"]))
                        )
                    else:
                        _tool_call_responses[rid] = request.get("result")
                    _tool_call_events[rid].set()
                continue
            rtype = request.get("type")
            if rtype == "execute":
                # Execute in a dedicated thread so stdin continues processing
                # tool results and other events without blocking.
                exec_thread = threading.Thread(
                    target=_execute_in_thread,
                    args=(request,),
                    name=f"ipython-exec-{request.get('id', 'cell')}",
                    daemon=True,
                )
                exec_thread.start()
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

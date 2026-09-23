"""Regression tests for the ipython runtime's trailing-expression display.

The runtime must not re-execute function calls or ``print()`` invocations
when displaying the trailing expression's repr — that would duplicate
output and confuse the model about what the cell actually produced.

These tests speak protocol v3 directly: streamed ``stdout`` frames plus a
``result`` frame for the trailing expression, and ``host_request`` /
``host_reply`` for the tool bridge.
"""

from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest


def _spawn_runtime() -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", "vtx.ai.agent.ipython_runtime"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        cwd=str(Path(__file__).resolve().parents[1]),
    )


# Reading a buffered text stream through ``select()`` hides lines already in
# the userspace buffer (a frame that arrives with the previous one never wakes
# the selector). One pump thread per process keeps the queue honest.
def _lines(process: subprocess.Popen) -> queue.Queue:
    # Hang the queue off the process itself: ``id()`` can be recycled once a
    # finished Popen is collected, which would hand a new kernel the old,
    # already-drained queue.
    existing = getattr(process, "_vtx_lines", None)
    if existing is not None:
        return existing
    q: queue.Queue = queue.Queue()

    def pump() -> None:
        try:
            for line in process.stdout:
                q.put(line)
        finally:
            q.put(None)

    threading.Thread(target=pump, daemon=True).start()
    process._vtx_lines = q  # type: ignore[attr-defined]
    return q


def _next(process: subprocess.Popen, deadline: float) -> str | None:
    """Next line before ``deadline``; ``None`` on EOF or timeout."""
    q = _lines(process)
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        try:
            line = q.get(timeout=min(remaining, 0.2))
        except queue.Empty:
            continue
        if line is None:
            return None
        return line


def _drain_ready(process: subprocess.Popen, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        line = _next(process, deadline)
        if not line:
            break
        if json.loads(line).get("event") == "ready":
            return
    raise RuntimeError("ipython runtime did not become ready in time")


def _send(process: subprocess.Popen, payload: dict) -> None:
    process.stdin.write(json.dumps(payload) + "\n")
    process.stdin.flush()


def _collect_until_done(process: subprocess.Popen, rid: str, timeout: float = 30.0) -> str:
    """Full cell text: streamed ``stdout`` plus the trailing expression's
    ``result`` frame (the kernel ships the repr there, without a newline)."""
    deadline = time.monotonic() + timeout
    text = ""
    while time.monotonic() < deadline:
        line = _next(process, deadline)
        if not line:
            break
        event = json.loads(line)
        if event.get("event") in ("stdout", "result"):
            text += event.get("text", "")
        elif event.get("event") == "done" and event.get("id") == rid:
            return text
    raise RuntimeError(f"timed out waiting for done rid={rid!r}; output so far: {text!r}")


def _await_host_request(process: subprocess.Popen, rid: str, timeout: float = 30.0) -> dict:
    """Read frames until the kernel asks the host for something, then return
    the request's ``data`` payload (with its ``id`` restored for the reply)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        line = _next(process, deadline)
        if not line:
            break
        event = json.loads(line)
        if event.get("event") == "host_request" and event.get("id"):
            data = event.get("data") or {}
            data["id"] = event["id"]
            return data
        if event.get("event") == "done" and event.get("id") == rid:
            break
    raise RuntimeError(f"no host_request arrived for rid={rid!r}")


@pytest.fixture
def runtime():
    proc = _spawn_runtime()
    _drain_ready(proc)
    try:
        yield proc
    finally:
        try:
            proc.stdin.write(json.dumps({"type": "shutdown", "id": uuid.uuid4().hex}) + "\n")
            proc.stdin.flush()
        except Exception:
            pass
        proc.stdin.close()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def test_print_call_runs_once(runtime):
    """``print(2 + 2)`` must produce exactly one ``4\\n`` and nothing else."""
    rid = uuid.uuid4().hex
    _send(runtime, {"type": "execute", "id": rid, "code": "print(2 + 2)"})
    text = _collect_until_done(runtime, rid)
    assert text == "4\n", f"expected '4\\n', got {text!r}"


def test_bare_expression_shows_repr(runtime):
    """A pure expression like ``2 + 2`` reports its repr on the ``result``
    frame: ``repr`` with no trailing newline, and no re-execution."""
    rid = uuid.uuid4().hex
    _send(runtime, {"type": "execute", "id": rid, "code": "2 + 2"})
    text = _collect_until_done(runtime, rid)
    assert text == "4", f"expected '4', got {text!r}"


def test_assignment_produces_no_output(runtime):
    """An assignment-only cell should produce no stdout (the model needs a
    positive synthetic confirmation, but that lives in the manager layer)."""
    rid = uuid.uuid4().hex
    _send(runtime, {"type": "execute", "id": rid, "code": "x = 5"})
    text = _collect_until_done(runtime, rid)
    assert text == "", f"expected '', got {text!r}"


def test_print_with_function_call_runs_once(runtime):
    """``print(len("abc"))`` must produce ``3\\n`` once, not twice."""
    rid = uuid.uuid4().hex
    _send(runtime, {"type": "execute", "id": rid, "code": 'print(len("abc"))'})
    text = _collect_until_done(runtime, rid)
    assert text == "3\n", f"expected '3\\n', got {text!r}"


def test_nested_call_does_not_reexecute(runtime):
    """``f(g(x))`` style calls must not be re-run by the trailing-expr block:
    the text is written exactly once, and the trailing expression's repr is
    reported on the ``result`` frame (``sys.stdout.write`` returns 5)."""
    rid = uuid.uuid4().hex
    _send(
        runtime, {"type": "execute", "id": rid, "code": "import sys; sys.stdout.write('once\\n')"}
    )
    text = _collect_until_done(runtime, rid)
    assert text.startswith("once\n"), f"expected stdout first, got {text!r}"
    assert text.count("once") == 1, f"cell body ran twice: {text!r}"
    assert text.endswith("5"), f"expected trailing repr '5', got {text!r}"


def test_attribute_access_shows_repr(runtime):
    """A bare attribute access expression should display its repr."""
    rid = uuid.uuid4().hex
    _send(runtime, {"type": "execute", "id": rid, "code": "import math\nmath.pi"})
    text = _collect_until_done(runtime, rid)
    assert text.startswith("3.14"), f"expected repr of pi, got {text!r}"


def _reply_ok(process: subprocess.Popen, request: dict, result) -> None:
    _send(
        process,
        {"type": "host_reply", "id": request["id"], "data": {"status": "ok", "result": result}},
    )


def test_tool_call_rpc_bridge(runtime):
    """``call_tool(...)`` must exchange ``host_request``/``host_reply``
    (protocol v3) without hanging and hand back the tool's value."""
    rid = uuid.uuid4().hex
    _send(
        runtime,
        {
            "type": "execute",
            "id": rid,
            "code": 'res = call_tool("web_search", query="HelpingAI")\nprint("SUCCESS:", res)',
        },
    )
    request = _await_host_request(runtime, rid)
    assert request["type"] == "tool.call"
    assert request["name"] == "web_search"
    assert request["args"] == {"query": "HelpingAI"}

    # Reply exactly the way the manager's host bridge does.
    _reply_ok(runtime, request, "Found HelpingAI")

    text = _collect_until_done(runtime, rid)
    assert "SUCCESS: Found HelpingAI" in text


def test_call_tool_error_reply_raises(runtime):
    """An ``error``-status reply must surface as a RuntimeError in the cell
    rather than silently returning the reply envelope."""
    rid = uuid.uuid4().hex
    _send(runtime, {"type": "execute", "id": rid, "code": "call_tool('nope')"})
    request = _await_host_request(runtime, rid)
    _send(
        runtime,
        {
            "type": "host_reply",
            "id": request["id"],
            "data": {"status": "error", "error": "tool nope is not available"},
        },
    )

    deadline = time.monotonic() + 30.0
    ename = evalue = None
    while time.monotonic() < deadline:
        line = _next(runtime, deadline)
        if not line:
            break
        event = json.loads(line)
        if event.get("event") == "error":
            ename, evalue = event.get("ename"), event.get("evalue")
            break
        if event.get("event") == "done" and event.get("id") == rid:
            break
    assert ename == "RuntimeError", f"expected RuntimeError, got {ename!r}"
    assert "tool nope is not available" in (evalue or "")


def test_async_host_request_round_trip(runtime):
    """Prime's skills call ``await host_request(...)`` — the async bridge must
    resolve through the event loop rather than the reader thread alone."""
    rid = uuid.uuid4().hex
    _send(
        runtime,
        {
            "type": "execute",
            "id": rid,
            "code": (
                "from vtx.ai.agent.rlm import host_request\n"
                "reply = await host_request('rlm.find_models', {'query': '', 'limit': 2})\n"
                "print('MODELS:', sorted(reply))"
            ),
        },
    )
    request = _await_host_request(runtime, rid)
    assert request["type"] == "rlm.find_models"
    _reply_ok(runtime, request, {"models": []})
    text = _collect_until_done(runtime, rid)
    assert "MODELS: ['models']" in text

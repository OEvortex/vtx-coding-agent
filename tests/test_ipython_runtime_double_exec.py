"""Regression tests for the ipython runtime's trailing-expression display.

The runtime must not re-execute function calls or ``print()`` invocations
when displaying the trailing expression's repr — that would duplicate
output and confuse the model about what the cell actually produced.
"""

from __future__ import annotations

import json
import subprocess
import sys
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


def _drain_ready(process: subprocess.Popen, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        line = process.stdout.readline()
        if not line:
            break
        event = json.loads(line)
        if event.get("event") == "ready":
            return
    raise RuntimeError("ipython runtime did not become ready in time")


def _send(process: subprocess.Popen, payload: dict) -> None:
    process.stdin.write(json.dumps(payload) + "\n")
    process.stdin.flush()


def _collect_until_done(process: subprocess.Popen, rid: str, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    stdout_text = ""
    while time.monotonic() < deadline:
        line = process.stdout.readline()
        if not line:
            break
        event = json.loads(line)
        if event.get("event") == "stdout":
            stdout_text += event.get("text", "")
        elif event.get("event") == "done" and event.get("id") == rid:
            return stdout_text
    raise RuntimeError(f"timed out waiting for done rid={rid!r}; stdout so far: {stdout_text!r}")


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
        proc.wait(timeout=5)


def test_print_call_runs_once(runtime):
    """``print(2 + 2)`` must produce exactly one ``4\\n`` and nothing else."""
    rid = uuid.uuid4().hex
    _send(runtime, {"type": "execute", "id": rid, "code": "print(2 + 2)"})
    stdout_text = _collect_until_done(runtime, rid)
    assert stdout_text == "4\n", f"expected '4\\n', got {stdout_text!r}"


def test_bare_expression_shows_repr(runtime):
    """A pure expression like ``2 + 2`` should display ``4\\n``."""
    rid = uuid.uuid4().hex
    _send(runtime, {"type": "execute", "id": rid, "code": "2 + 2"})
    stdout_text = _collect_until_done(runtime, rid)
    assert stdout_text == "4\n", f"expected '4\\n', got {stdout_text!r}"


def test_assignment_produces_no_output(runtime):
    """An assignment-only cell should produce no stdout (the model needs a
    positive synthetic confirmation, but that lives in the manager layer)."""
    rid = uuid.uuid4().hex
    _send(runtime, {"type": "execute", "id": rid, "code": "x = 5"})
    stdout_text = _collect_until_done(runtime, rid)
    assert stdout_text == "", f"expected '', got {stdout_text!r}"


def test_print_with_function_call_runs_once(runtime):
    """``print(len("abc"))`` must produce ``3\\n`` once, not twice."""
    rid = uuid.uuid4().hex
    _send(runtime, {"type": "execute", "id": rid, "code": 'print(len("abc"))'})
    stdout_text = _collect_until_done(runtime, rid)
    assert stdout_text == "3\n", f"expected '3\\n', got {stdout_text!r}"


def test_nested_call_does_not_reexecute(runtime):
    """``f(g(x))`` style calls must not be re-run by the trailing-expr block."""
    rid = uuid.uuid4().hex
    _send(
        runtime, {"type": "execute", "id": rid, "code": "import sys; sys.stdout.write('once\\n')"}
    )
    stdout_text = _collect_until_done(runtime, rid)
    assert stdout_text == "once\n", f"expected 'once\\n', got {stdout_text!r}"


def test_attribute_access_shows_repr(runtime):
    """A bare attribute access expression should display its repr."""
    rid = uuid.uuid4().hex
    _send(runtime, {"type": "execute", "id": rid, "code": "import math\nmath.pi"})
    stdout_text = _collect_until_done(runtime, rid)
    assert stdout_text.startswith("3.14"), f"expected repr of pi, got {stdout_text!r}"


def test_tool_call_rpc_bridge(runtime):
    """Calling a tool helper like web_search must exchange tool_call and tool_result without
    hanging."""
    rid = uuid.uuid4().hex
    _send(
        runtime,
        {
            "type": "execute",
            "id": rid,
            "code": 'res = call_tool("web_search", query="HelpingAI")\nprint("SUCCESS:", res)',
        },
    )
    # Read until we see the tool_call event
    deadline = time.monotonic() + 5.0
    tool_id = None
    while time.monotonic() < deadline:
        line = runtime.stdout.readline()
        if not line:
            break
        event = json.loads(line)
        if event.get("event") == "tool_call":
            tool_id = event.get("id")
            assert event.get("name") == "web_search"
            assert event.get("args") == {"query": "HelpingAI"}
            break
    assert tool_id is not None, "Did not receive tool_call event"

    # Send tool_result back
    _send(runtime, {"event": "tool_result", "id": tool_id, "result": "Found HelpingAI"})

    # Collect done
    stdout_text = _collect_until_done(runtime, rid)
    assert "SUCCESS: Found HelpingAI" in stdout_text

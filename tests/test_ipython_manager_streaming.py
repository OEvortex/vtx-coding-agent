"""Tests for the streaming protocol emitted by ``IpythonKernel._wait_output``.

We exercise ``_wait_output`` directly by injecting JSONL events into the
kernel's queue. The subprocess is never spawned.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import stat
from typing import Any
from unittest import mock

import pytest

from vtx.ai.agent import ipython_manager
from vtx.ai.agent.ipython_manager import IpythonKernel, IpythonManager, KernelPool
from vtx.tui.ipython_block import TAG_DONE, TAG_ERROR, TAG_STDOUT


class _FakeProcess:
    returncode = None

    class _Stdin:
        async def write(self, data: bytes) -> None:
            return None

        async def drain(self) -> None:
            return None

    stdin = _Stdin()


async def _run_with_delivery(events: list[dict], *, timeout: float = 5.0):
    """Run ``_wait_output`` against the given event stream; return
    ``((final_output_text, errored), delivered_callback_strings)``."""
    kernel = IpythonKernel(kernel_id="test", cwd=".")
    kernel._process = _FakeProcess()  # type: ignore[assignment]
    kernel._queue = asyncio.Queue()
    delivered: list[str] = []

    async def feed():
        for event in events:
            await kernel._queue.put(json.dumps(event))
        await kernel._queue.put(None)

    async def on_output(text: str) -> None:
        delivered.append(text)

    feed_task = asyncio.create_task(feed())
    try:
        result = await kernel._wait_output(
            "rid", asyncio.Event(), on_output=on_output, timeout=timeout
        )
    finally:
        feed_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await feed_task
    return result, delivered


@pytest.mark.asyncio
async def test_emits_stdout_per_event_not_per_buffer():
    events = [
        {"event": "stdout", "id": "cell-1", "text": "hello "},
        {"event": "stdout", "id": "cell-1", "text": "world\n"},
        {"event": "done", "id": "rid", "status": "ok"},
    ]
    (output, errored), delivered = await _run_with_delivery(events)
    stdout_calls = [d for d in delivered if d.startswith(TAG_STDOUT)]
    assert stdout_calls == [f"{TAG_STDOUT}hello ", f"{TAG_STDOUT}world\n"]
    assert output == "hello world"
    assert errored is False


@pytest.mark.asyncio
async def test_emits_stderr():
    events = [
        {"event": "stderr", "id": "cell-1", "text": "warning\n"},
        {"event": "done", "id": "rid", "status": "ok"},
    ]
    (output, errored), delivered = await _run_with_delivery(events)
    stderr_calls = [d for d in delivered if d.startswith("__STDERR__")]
    assert stderr_calls == ["__STDERR__warning\n"]
    assert output == "warning"
    assert errored is False


@pytest.mark.asyncio
async def test_emits_result_repr():
    """The assembled tool result is whitespace-stripped before it reaches the
    model (stream frames are delivered raw)."""
    events = [
        {"event": "result", "id": "cell-1", "text": "42\n"},
        {"event": "done", "id": "rid", "status": "ok"},
    ]
    (output, errored), delivered = await _run_with_delivery(events)
    assert [d for d in delivered if d.startswith("__RESULT__")] == ["__RESULT__42\n"]
    assert output == "42"
    assert errored is False


@pytest.mark.asyncio
async def test_emits_error_block_with_traceback():
    events = [
        {
            "event": "error",
            "id": "cell-1",
            "ename": "NameError",
            "evalue": "name 'x' is not defined",
            "traceback": [
                "Traceback (most recent call last):",
                "  File '<cell-1>'",
                "NameError: x",
            ],
        },
        {"event": "done", "id": "rid", "status": "error"},
    ]
    (output, errored), delivered = await _run_with_delivery(events)
    error_calls = [d for d in delivered if d.startswith(TAG_ERROR)]
    assert error_calls, "expected an __ERROR__ event to be emitted"
    text = error_calls[0][len(TAG_ERROR) :]
    assert "NameError" in text
    assert "Traceback" in text
    assert "NameError: x" in text
    assert "name 'x' is not defined" in output
    assert errored is True


@pytest.mark.asyncio
async def test_emits_done_marker_as_last_callback():
    events = [
        {"event": "stdout", "id": "cell-1", "text": "x"},
        {"event": "done", "id": "rid", "status": "ok"},
    ]
    _result, delivered = await _run_with_delivery(events)
    assert delivered[-1] == TAG_DONE


@pytest.mark.asyncio
async def test_empty_stdout_does_not_emit_callback():
    events = [
        {"event": "stdout", "id": "cell-1", "text": ""},
        {"event": "done", "id": "rid", "status": "ok"},
    ]
    _result, delivered = await _run_with_delivery(events)
    stdout_calls = [d for d in delivered if d.startswith(TAG_STDOUT)]
    assert stdout_calls == []


@pytest.mark.asyncio
async def test_done_for_other_rid_is_ignored():
    """Other-request done events must not stop our wait loop."""
    events = [
        {"event": "done", "id": "some-other-request", "status": "ok"},
        {"event": "stdout", "id": "cell-1", "text": "ours\n"},
        {"event": "done", "id": "rid", "status": "ok"},
    ]
    (output, errored), delivered = await _run_with_delivery(events)
    assert output == "ours"
    assert errored is False
    assert delivered[-1] == TAG_DONE


@pytest.mark.asyncio
async def test_wedged_cell_is_reported_and_marks_the_kernel():
    """A cell that ignores the interrupt wedges the kernel.

    The kernel stays alive but its serve loop is still blocked on the cell, so
    every later cell for the session would queue behind it and time out the
    same way. The host must mark it and say plainly that the namespace is gone,
    rather than reporting a plain timeout and leaving the session poisoned.
    """
    kernel = IpythonKernel(kernel_id="test", cwd=".")
    kernel._process = _FakeProcess()  # type: ignore[assignment]
    kernel._queue = asyncio.Queue()
    written: list[bytes] = []
    kernel._process.stdin.write = lambda data: written.append(data)  # type: ignore[method-assign]

    delivered: list[str] = []
    with (
        mock.patch.object(ipython_manager, "_ABORT_GRACE_SECONDS", 0.05),
        mock.patch.object(kernel, "_send", new=_recording_send(written)),
    ):
        # No events at all: the cell never finishes and never dies.
        output, errored = await kernel._wait_output(
            "rid", asyncio.Event(), on_output=stream_collector(delivered), timeout=0.05
        )

    assert errored is True
    assert kernel.is_wedged() is True
    assert "did not respond to the interrupt" in output
    assert "restarted" in output
    assert any("did not respond to the interrupt" in text for text in delivered)
    # The interrupt was actually sent, otherwise the kernel was never asked.
    assert any(json.loads(w.decode())["type"] == "interrupt" for w in written)


@pytest.mark.asyncio
async def test_interrupt_acknowledged_inside_grace_does_not_wedge():
    """A cell that reports ``done`` within the grace period is not wedged.

    SIGINT can be slow to land in a tight loop or a C call, so the grace window
    is what separates "slow to stop" from "cannot be stopped". A cell that
    reports inside the window must not cost the model its namespace.
    """
    kernel = IpythonKernel(kernel_id="test", cwd=".")
    kernel._process = _FakeProcess()  # type: ignore[assignment]
    kernel._queue = asyncio.Queue()

    async def feed() -> None:
        await asyncio.sleep(0.15)
        await kernel._queue.put(json.dumps({"event": "done", "id": "rid", "status": "ok"}))
        await kernel._queue.put(None)

    feed_task = asyncio.create_task(feed())
    try:
        with (
            mock.patch.object(ipython_manager, "_ABORT_GRACE_SECONDS", 1.0),
            mock.patch.object(kernel, "_send", new=_recording_send([])),
        ):
            output, errored = await kernel._wait_output(
                "rid", asyncio.Event(), on_output=None, timeout=0.05
            )
    finally:
        feed_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await feed_task

    assert errored is True  # the cell did time out
    assert kernel.is_wedged() is False
    assert "did not respond" not in output


@pytest.mark.asyncio
async def test_dead_kernel_on_timeout_is_not_reported_as_wedged():
    """A kernel that died during the grace period needs no restart notice."""

    class _DeadProcess:
        stdin = None
        returncode = 1

    kernel = IpythonKernel(kernel_id="test", cwd=".")
    kernel._queue = asyncio.Queue()
    kernel._process = _DeadProcess()  # type: ignore[assignment]
    with mock.patch.object(ipython_manager, "_ABORT_GRACE_SECONDS", 0.05):
        output, _errored = await kernel._wait_output(
            "rid", asyncio.Event(), on_output=None, timeout=0.05
        )
    assert kernel.is_wedged() is False
    assert "did not respond" not in output


@pytest.mark.asyncio
async def test_oversized_output_spills_to_a_readable_file(monkeypatch):
    """Truncation alone loses work; the full text must stay reachable."""
    monkeypatch.setattr(ipython_manager, "_OUTPUT_TRUNCATE_BYTES", 200)
    body = "x" * 5000
    events = [
        {"event": "stdout", "id": "cell-1", "text": body},
        {"event": "done", "id": "rid", "status": "ok"},
    ]
    (output, errored), _ = await _run_with_delivery(events)
    assert errored is False
    assert len(output) < len(body) + 400  # truncated, not passed through whole
    match = re.search(r"\[Full cell output: (\S+) \(read it with", output)
    assert match, output
    path = match.group(1)
    try:
        assert os.path.exists(path)
        assert open(path, encoding="utf-8").read() == body
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    finally:
        os.unlink(path)


@pytest.mark.asyncio
async def test_stream_cap_spills_the_full_stream(monkeypatch):
    """A capped stream keeps its head, and the dropped tail lands in a file."""
    monkeypatch.setattr(ipython_manager, "_MAX_STREAM_CHARS", 100)
    body = "y" * 4000
    events = [
        {"event": "stdout", "id": "cell-1", "text": body},
        {"event": "done", "id": "rid", "status": "ok"},
    ]
    (output, _errored), _ = await _run_with_delivery(events)
    assert ipython_manager._STREAM_TRUNCATION_MARKER in output
    match = re.search(r"\[Full stdout: (\S+) \(read it with", output)
    assert match, output
    path = match.group(1)
    try:
        assert open(path, encoding="utf-8").read() == body
    finally:
        os.unlink(path)


@pytest.mark.asyncio
async def test_short_output_is_not_spilled():
    events = [
        {"event": "stdout", "id": "cell-1", "text": "small\n"},
        {"event": "done", "id": "rid", "status": "ok"},
    ]
    (output, _errored), _ = await _run_with_delivery(events)
    assert output == "small"
    assert "Full cell output" not in output
    assert "Full stdout" not in output


@pytest.mark.asyncio
async def test_manager_replaces_a_wedged_kernel_instead_of_reusing_it():
    """The wedge is terminal for the process, not for the session."""
    manager = IpythonManager(cwd=".")
    wedged = IpythonKernel(kernel_id="wedged", cwd=".")
    wedged._wedged = True
    closed: list[str] = []
    wedged.close = _record_close(closed, "wedged")  # type: ignore[method-assign]

    fresh = IpythonKernel(kernel_id="fresh", cwd=".")

    async def fake_acquire(session_id: str) -> IpythonKernel:
        return fresh

    async def fake_execute(*args: Any, **kwargs: Any) -> tuple[str, bool]:
        return ("ok", False)

    manager._pool.acquire = fake_acquire  # type: ignore[method-assign]
    manager._pool.release = _noop_release  # type: ignore[method-assign]
    fresh.execute = fake_execute  # type: ignore[method-assign]
    manager._session_kernels["sess"] = wedged

    output, errored = await manager.execute("sess", "1")

    assert (output, errored) == ("ok", False)
    assert closed == ["wedged"], "the wedged process must be killed, not pooled"
    assert manager._session_kernels["sess"] is fresh


@pytest.mark.asyncio
async def test_pool_refuses_to_recycle_a_wedged_kernel():
    pool = KernelPool(cwd=".", pool_size=2)
    wedged = IpythonKernel(kernel_id="wedged", cwd=".")
    wedged._wedged = True
    closed: list[str] = []
    wedged.close = _record_close(closed, "wedged")  # type: ignore[method-assign]
    pool._in_use["sess"] = wedged

    await pool.release("sess")

    assert closed == ["wedged"]
    assert pool._kernels == []


@pytest.mark.asyncio
async def test_gc_collects_a_wedged_kernel():
    manager = IpythonManager(cwd=".")
    wedged = IpythonKernel(kernel_id="wedged", cwd=".")
    wedged._wedged = True
    closed: list[str] = []
    wedged.close = _record_close(closed, "wedged")  # type: ignore[method-assign]
    manager._session_kernels["sess"] = wedged
    manager._pool.release = _noop_release  # type: ignore[method-assign]

    await manager._gc_loop_once()

    assert closed == ["wedged"]
    assert "sess" not in manager._session_kernels


def _record_close(into: list[str], name: str):
    async def close() -> None:
        into.append(name)

    return close


async def _noop_release(session_id: str) -> None:
    return None


def _recording_send(written: list[bytes]):
    async def send(payload: dict[str, Any]) -> bool:
        written.append(json.dumps(payload).encode("utf-8"))
        return True

    return send


def stream_collector(into: list[str]):
    async def on_output(text: str) -> None:
        into.append(text)

    return on_output


@pytest.mark.asyncio
async def test_empty_cell_synthesizes_positive_confirmation():
    """A cell that ran cleanly but produced no stdout/result must still
    return a non-empty tool result so the model doesn't conclude the cell
    did nothing."""
    events = [{"event": "done", "id": "rid", "status": "ok"}]
    (output, _errored), _ = await _run_with_delivery(events)
    assert output  # not empty
    assert "successfully" in output.lower() or "no output" in output.lower()


@pytest.mark.asyncio
async def test_host_request_event_dispatches_to_host_bridge():
    """A ``host_request`` frame must be routed to the host bridge and answered
    with a ``host_reply`` frame carrying the typed reply envelope."""
    executor_calls: list[tuple[str, dict[str, Any]]] = []

    async def fake_executor(name: str, args: dict[str, Any]) -> Any:
        executor_calls.append((name, args))
        return {"ok": True, "echo": args}

    dispatched: list[dict[str, Any]] = []

    async def fake_dispatch(
        data: dict[str, Any], *, tool_executor: Any = None, session_id: str | None = None
    ) -> dict[str, Any]:
        dispatched.append(data)
        assert session_id == "sess-1"
        assert data["type"] == "tool.call"
        assert tool_executor is fake_executor
        result = await tool_executor(data["name"], data["args"])
        return {"status": "ok", "result": result}

    kernel = IpythonKernel(kernel_id="test", cwd=".", host_dispatcher=fake_dispatch)
    kernel._process = _FakeProcess()  # type: ignore[assignment]
    kernel._queue = asyncio.Queue()
    kernel._tool_executor = fake_executor
    kernel.set_session("sess-1")

    written: list[bytes] = []
    kernel._process.stdin.write = lambda data: written.append(data)  # type: ignore[method-assign]

    async def feed():
        await kernel._queue.put(
            json.dumps(
                {
                    "event": "host_request",
                    "id": "req-1",
                    "data": {
                        "type": "tool.call",
                        "name": "web_search",
                        "args": {"query": "hello", "num_results": 2},
                    },
                }
            )
        )
        await kernel._queue.put(json.dumps({"event": "done", "id": "rid-1", "status": "ok"}))
        await kernel._queue.put(None)

    feed_task = asyncio.create_task(feed())
    try:
        result = await kernel._wait_output("rid-1", asyncio.Event(), on_output=None, timeout=5.0)
    finally:
        feed_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await feed_task

    assert result[1] is False
    assert executor_calls == [("web_search", {"query": "hello", "num_results": 2})]
    assert dispatched and dispatched[0]["type"] == "tool.call"
    assert written, "expected manager to write a host_reply frame"
    response = json.loads(written[0].decode("utf-8"))
    assert response["type"] == "host_reply"
    assert response["id"] == "req-1"
    assert response["data"] == {
        "status": "ok",
        "result": {"ok": True, "echo": {"query": "hello", "num_results": 2}},
    }


@pytest.mark.asyncio
async def test_host_bridge_failure_becomes_error_reply():
    """A bridge that raises (or is missing) must produce an ``error`` reply —
    never an unwritten request that would wedge the kernel forever."""

    async def broken_dispatch(data: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("bridge exploded")

    kernel = IpythonKernel(kernel_id="test", cwd=".", host_dispatcher=broken_dispatch)
    kernel._process = _FakeProcess()  # type: ignore[assignment]
    kernel._queue = asyncio.Queue()

    written: list[bytes] = []
    kernel._process.stdin.write = lambda data: written.append(data)  # type: ignore[method-assign]

    await kernel._handle_host_request(
        {"event": "host_request", "id": "req-2", "data": {"type": "rlm.run"}}, None
    )
    assert written, "expected an error host_reply even when the bridge raises"
    response = json.loads(written[0].decode("utf-8"))
    assert response["type"] == "host_reply"
    assert response["id"] == "req-2"
    assert response["data"]["status"] == "error"
    assert "bridge exploded" in response["data"]["error"]


@pytest.mark.asyncio
async def test_hanging_host_request_does_not_outlive_cell_timeout():
    """A bridge call that never returns must not outlive the cell deadline.

    The dispatch is awaited inline in the drain loop, so an unbounded bridge
    (a hung bash/task behind ``tool.call``) would stall the loop, the kernel's
    ``done`` would never be read, and the execution lock would stay held for
    every later cell — a permanent hang. The cell must time out instead, and
    the kernel must still get its ``host_reply`` or it waits forever in turn.
    """

    async def hanging_dispatch(data: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        await asyncio.sleep(3600)
        return {"status": "ok", "result": None}

    kernel = IpythonKernel(kernel_id="test", cwd=".", host_dispatcher=hanging_dispatch)
    kernel._process = _FakeProcess()  # type: ignore[assignment]
    kernel._queue = asyncio.Queue()

    written: list[bytes] = []
    kernel._process.stdin.write = lambda data: written.append(data)  # type: ignore[method-assign]

    async def feed() -> None:
        await kernel._queue.put(
            json.dumps(
                {
                    "event": "host_request",
                    "id": "req-hang",
                    "data": {"type": "tool.call", "name": "bash", "args": {}},
                }
            )
        )
        await kernel._queue.put(json.dumps({"event": "done", "id": "rid-hang", "status": "ok"}))
        await kernel._queue.put(None)

    feed_task = asyncio.create_task(feed())
    try:
        # Generous outer bound: the cell timeout is 0.3s, so a regression
        # blocks here instead of hanging the suite.
        output, errored = await asyncio.wait_for(
            kernel._wait_output("rid-hang", asyncio.Event(), on_output=None, timeout=0.3),
            timeout=10.0,
        )
    finally:
        feed_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await feed_task

    assert errored is True
    assert "timed out" in output.lower()
    replies = [
        json.loads(chunk.decode("utf-8"))
        for chunk in written
        if json.loads(chunk.decode("utf-8")).get("type") == "host_reply"
    ]
    assert [r["id"] for r in replies] == ["req-hang"]
    assert replies[0]["data"]["status"] == "error"
    assert "timed out" in replies[0]["data"]["error"]


@pytest.mark.asyncio
async def test_result_event_with_no_stdout_returns_repr():
    events = [
        {"event": "result", "id": "cell-1", "text": "hello\n"},
        {"event": "done", "id": "rid", "status": "ok"},
    ]
    (output, errored), _ = await _run_with_delivery(events)
    assert output == "hello"
    assert errored is False


@pytest.mark.asyncio
async def test_timed_out_cell_is_marked_errored():
    """If the cell exceeds timeout, the returned tuple marks errored=True
    so the model can retry."""

    async def slow_feed():
        # Pump enough stdout to keep the loop busy but never send ``done``
        # — the timeout in ``_wait_output`` should fire first.
        while True:
            await asyncio.sleep(0.05)
            await kernel._queue.put(  # type: ignore[has-type]
                json.dumps({"event": "stdout", "id": "cell-1", "text": "y\n"})
            )

    kernel = IpythonKernel(kernel_id="test", cwd=".")
    kernel._process = _FakeProcess()
    kernel._queue = asyncio.Queue()
    feed_task = asyncio.create_task(slow_feed())
    try:
        output, errored = await kernel._wait_output(
            "rid", asyncio.Event(), on_output=None, timeout=0.3
        )
    finally:
        feed_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await feed_task
    assert errored is True
    assert "timed out" in output.lower()

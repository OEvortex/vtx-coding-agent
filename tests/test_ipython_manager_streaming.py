"""Tests for the streaming protocol emitted by ``IpythonKernel._wait_output``.

We exercise ``_wait_output`` directly by injecting JSONL events into the
kernel's queue. The subprocess is never spawned.
"""

from __future__ import annotations

import asyncio
import contextlib
import json

import pytest

from vtx.ai.agent.ipython_manager import IpythonKernel
from vtx.tui.ipython_block import TAG_DONE, TAG_ERROR, TAG_STDOUT


class _FakeProcess:
    returncode = None


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
    events = [
        {"event": "result", "id": "cell-1", "text": "42\n"},
        {"event": "done", "id": "rid", "status": "ok"},
    ]
    (output, errored), _ = await _run_with_delivery(events)
    assert output == "42\n"
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
async def test_empty_cell_synthesizes_positive_confirmation():
    """A cell that ran cleanly but produced no stdout/result must still
    return a non-empty tool result so the model doesn't conclude the cell
    did nothing."""
    events = [{"event": "done", "id": "rid", "status": "ok"}]
    (output, errored), _ = await _run_with_delivery(events)
    assert output  # not empty
    assert "successfully" in output.lower() or "no output" in output.lower()
    assert errored is False


@pytest.mark.asyncio
async def test_result_event_with_no_stdout_returns_repr():
    events = [
        {"event": "result", "id": "cell-1", "text": "hello\n"},
        {"event": "done", "id": "rid", "status": "ok"},
    ]
    (output, errored), _ = await _run_with_delivery(events)
    assert output == "hello\n"
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

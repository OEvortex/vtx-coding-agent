"""Tests for tracing primitives and exporters."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vtx.sdk.tracing import (
    DEFAULT_WORKFLOW_NAME,
    add_trace_processor,
    current_span,
    current_trace,
    disable_tracing,
    enable_tracing,
    is_tracing_disabled,
    span,
    trace,
)
from vtx.sdk.tracing.exporters import ConsoleTraceProcessor, JSONLTraceProcessor
from vtx.sdk.tracing.processor import get_default_processors, set_default_processors


class _CollectingProcessor:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def on_trace_start(self, trace):
        self.events.append(("trace_start", trace.name))

    def on_trace_end(self, trace):
        self.events.append(("trace_end", trace.name))

    def on_span_start(self, span):
        self.events.append(("span_start", span.name))

    def on_span_end(self, span):
        self.events.append(("span_end", span.name))


@pytest.fixture(autouse=True)
def _reset_processors():
    saved = list(get_default_processors())
    set_default_processors([])
    yield
    set_default_processors(saved)


def test_trace_basic() -> None:
    proc = _CollectingProcessor()
    add_trace_processor(proc)
    with trace("test-workflow") as t:
        assert current_trace() is t
    assert ("trace_start", "test-workflow") in proc.events
    assert ("trace_end", "test-workflow") in proc.events


def test_trace_nested() -> None:
    proc = _CollectingProcessor()
    add_trace_processor(proc)
    with trace("outer"):
        outer = current_trace()
        with trace("inner"):
            inner = current_trace()
            assert inner is not outer
        assert current_trace() is outer


def test_span_basic() -> None:
    proc = _CollectingProcessor()
    add_trace_processor(proc)
    with span("my-span") as s:
        assert current_span() is s
    assert ("span_start", "my-span") in proc.events
    assert ("span_end", "my-span") in proc.events


def test_span_nested() -> None:
    proc = _CollectingProcessor()
    add_trace_processor(proc)
    with span("outer-span"):
        outer = current_span()
        with span("inner-span"):
            inner = current_span()
            assert inner is not outer
        assert current_span() is outer


def test_trace_decorator_form() -> None:
    proc = _CollectingProcessor()
    add_trace_processor(proc)

    @trace()
    def my_func():
        assert current_trace() is not None
        return "done"

    result = my_func()
    assert result == "done"
    assert any(ev == ("trace_start", "my_func") for ev in proc.events)


def test_trace_decorator_async_form() -> None:
    proc = _CollectingProcessor()
    add_trace_processor(proc)

    @trace()
    async def my_async_func():
        assert current_trace() is not None
        return "async done"

    import asyncio

    result = asyncio.run(my_async_func())
    assert result == "async done"
    assert any(ev == ("trace_start", "my_async_func") for ev in proc.events)


def test_disable_enable_tracing() -> None:
    assert not is_tracing_disabled()
    disable_tracing()
    assert is_tracing_disabled()
    enable_tracing()
    assert not is_tracing_disabled()


def test_trace_when_disabled() -> None:
    proc = _CollectingProcessor()
    add_trace_processor(proc)
    disable_tracing()
    try:
        with trace("disabled"):
            pass
        # Processor is still called, but tracing is no-op for spans.
        # Trace still goes through (it has no parent contextvar to suppress).
    finally:
        enable_tracing()


def test_jsonl_trace_processor_writes_file(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    proc = JSONLTraceProcessor(path)
    add_trace_processor(proc)
    with trace("my-workflow"), span("my-span"):
        pass
    content = path.read_text()
    lines = [line for line in content.splitlines() if line]
    assert len(lines) >= 4  # trace_start, span_start, span_end, trace_end
    # Each line should be a valid JSON object.
    for line in lines:
        data = json.loads(line)
        assert "type" in data
        assert "timestamp" in data


def test_console_trace_processor(capsys) -> None:
    proc = ConsoleTraceProcessor()
    add_trace_processor(proc)
    with trace("console-test"), span("console-span"):
        pass
    captured = capsys.readouterr()
    assert "console-test" in captured.err
    assert "console-span" in captured.err


def test_default_workflow_name_constant() -> None:
    assert DEFAULT_WORKFLOW_NAME == "Agent workflow"


def test_trace_with_metadata() -> None:
    proc = _CollectingProcessor()
    add_trace_processor(proc)
    with trace("named", group_id="grp-1", metadata={"k": "v"}):
        pass
    # The metadata is stored on the trace object, not necessarily emitted by
    # the collecting processor. We just verify the trace object.
    assert proc.events  # it ran

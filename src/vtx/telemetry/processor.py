"""Trace processor protocol and the default in-process processor chain."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class TraceProcessor(Protocol):
    """A sink for ``Trace`` and ``Span`` lifecycle events.

    Implementations receive ``on_trace_start``/``on_trace_end`` and
    ``on_span_start``/``on_span_end`` calls. The SDK never raises out
    of these handlers — processors must swallow their own exceptions.
    """

    def on_trace_start(self, trace: Any) -> None: ...
    def on_trace_end(self, trace: Any) -> None: ...
    def on_span_start(self, span: Any) -> None: ...
    def on_span_end(self, span: Any) -> None: ...


_default_processors: list[TraceProcessor] = []


def get_default_processors() -> list[TraceProcessor]:
    return list(_default_processors)


def set_default_processors(processors: list[TraceProcessor]) -> None:
    global _default_processors
    _default_processors = list(processors)


__all__ = ["TraceProcessor", "get_default_processors", "set_default_processors"]

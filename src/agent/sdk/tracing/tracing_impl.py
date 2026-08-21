"""Tracing primitives: ``Trace`` and ``Span`` context managers.

Mirrors the OpenAI Agents SDK shape (trace + nested spans) but uses an
in-process processor chain. Default processor is ``[]`` until the user
adds one (e.g. :class:`ConsoleTraceProcessor`).
"""

from __future__ import annotations

import contextvars
import functools
import inspect
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from typing import Any

from .processor import get_default_processors, set_default_processors

DEFAULT_WORKFLOW_NAME = "Agent workflow"

_disabled = False
_current_trace: contextvars.ContextVar[Trace | None] = contextvars.ContextVar(
    "vtx_sdk_current_trace", default=None
)
_current_span: contextvars.ContextVar[Span | None] = contextvars.ContextVar(
    "vtx_sdk_current_span", default=None
)


def is_tracing_disabled() -> bool:
    return _disabled or _env_disabled()


def _env_disabled() -> bool:
    import os

    return os.environ.get("VTX_SDK_DISABLE_TRACING", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def disable_tracing() -> None:
    global _disabled
    _disabled = True


def enable_tracing() -> None:
    global _disabled
    _disabled = False


def add_trace_processor(processor: Any) -> None:
    processors = list(get_default_processors())
    processors.append(processor)
    set_default_processors(processors)


def set_trace_processors(processors: list[Any]) -> None:
    set_default_processors(list(processors))


@dataclass
class Trace:
    """A trace is the top-level container for a workflow run.

    Use as a context manager::

        with trace("My workflow"):
            result = Runner.run_sync(agent, "...")

    Traces can be nested; a nested :class:`Trace` becomes a span of its
    parent. Spans created inside the ``with`` block attach to this
    trace (or the nearest enclosing one).
    """

    name: str = DEFAULT_WORKFLOW_NAME
    trace_id: str = field(default_factory=lambda: f"trace_{uuid.uuid4().hex[:32]}")
    group_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    _parent: Trace | None = field(default=None, repr=False, compare=False)
    _started_at: float = field(default=0.0, repr=False)
    _ended_at: float = field(default=0.0, repr=False)

    def __enter__(self) -> Trace:
        global _current_trace
        self._parent = _current_trace.get()
        self._started_at = time.time()
        _current_trace.set(self)
        for processor in get_default_processors():
            with suppress(Exception):
                processor.on_trace_start(self)
        return self

    def __exit__(self, *exc_info: Any) -> None:
        global _current_trace
        self._ended_at = time.time()
        for processor in get_default_processors():
            with suppress(Exception):
                processor.on_trace_end(self)
        _current_trace.set(self._parent)

    def finish(self) -> None:
        self.__exit__()


def current_trace() -> Trace | None:
    return _current_trace.get()


@dataclass
class Span:
    """A span records a single operation within a trace."""

    name: str
    span_id: str = field(default_factory=lambda: f"span_{uuid.uuid4().hex[:24]}")
    parent_id: str | None = None
    trace_id: str | None = None
    span_data: Any = None
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def finish(self) -> None:
        self.ended_at = time.time()
        for processor in get_default_processors():
            with suppress(Exception):
                processor.on_span_end(self)


def current_span() -> Span | None:
    return _current_span.get()


@contextmanager
def span(name: str, *, span_data: Any = None, **metadata: Any) -> Iterator[Span]:
    """Context manager that creates a span and registers it with the active trace."""
    global _current_span

    if is_tracing_disabled():
        noop = Span(name=name, span_data=span_data)
        yield noop
        return

    parent = _current_span.get()
    trace = _current_trace.get()
    s = Span(
        name=name,
        parent_id=parent.span_id if parent else None,
        trace_id=trace.trace_id if trace else None,
        span_data=span_data,
        metadata=metadata,
    )
    for processor in get_default_processors():
        with suppress(Exception):
            processor.on_span_start(s)
    token = _current_span.set(s)
    try:
        yield s
    finally:
        s.finish()
        _current_span.reset(token)


_DECORATOR_MODE = object()


def trace(  # type: ignore[no-redef]
    name: Any = _DECORATOR_MODE,
    *,
    group_id: str | None = None,
    metadata: dict[str, Any] | None = None,
):
    """Context manager / decorator: open a :class:`Trace`.

    As a context manager::

        with trace("my-workflow"):
            ...

    As a decorator (with or without parens)::

        @trace
        async def my_workflow():
            ...

        @trace()
        async def my_workflow():
            ...
    """

    if callable(name):
        return _make_trace_decorator(name)
    if name is _DECORATOR_MODE or name is None:
        return _make_unnamed_decorator()
    if not isinstance(name, str):
        name = DEFAULT_WORKFLOW_NAME
    return Trace(name=name, group_id=group_id, metadata=metadata or {})


def _make_trace_decorator(func: Callable[..., Any]) -> Callable[..., Any]:
    """``@trace`` form: trace name is the wrapped function's name."""

    @functools.wraps(func)
    def _wrap(*args: Any, **kwargs: Any) -> Any:
        with Trace(name=getattr(func, "__name__", "agent_workflow")):
            return func(*args, **kwargs)

    if inspect.iscoroutinefunction(func):

        @functools.wraps(func)
        async def _wrap_async(*args: Any, **kwargs: Any) -> Any:
            with Trace(name=getattr(func, "__name__", "agent_workflow")):
                return await func(*args, **kwargs)

        return _wrap_async
    return _wrap


def _make_unnamed_decorator() -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """``@trace()`` / ``@trace(None)`` form: returns a decorator that
    uses the wrapped function's name as the trace name.
    """

    def _decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        return _make_trace_decorator(func)

    return _decorator


__all__ = [
    "DEFAULT_WORKFLOW_NAME",
    "Span",
    "Trace",
    "add_trace_processor",
    "current_span",
    "current_trace",
    "disable_tracing",
    "enable_tracing",
    "is_tracing_disabled",
    "set_trace_processors",
    "span",
    "trace",
]

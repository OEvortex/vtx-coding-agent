"""Tracing package: ``Trace`` / ``Span`` primitives + processor chain."""

from .processor import TraceProcessor, get_default_processors, set_default_processors  # noqa: F401

# Re-export the public tracing API from the top-level ``vtx.sdk.tracing``
# module path used by the runner and user code.
from .tracing_impl import (
    DEFAULT_WORKFLOW_NAME,
    Span,
    Trace,
    add_trace_processor,
    current_span,
    current_trace,
    disable_tracing,
    enable_tracing,
    is_tracing_disabled,
    set_trace_processors,
    span,
    trace,
)

__all__ = [
    "DEFAULT_WORKFLOW_NAME",
    "Span",
    "Trace",
    "TraceProcessor",
    "add_trace_processor",
    "current_span",
    "current_trace",
    "disable_tracing",
    "enable_tracing",
    "get_default_processors",
    "is_tracing_disabled",
    "set_trace_processors",
    "set_trace_processors",
    "span",
    "trace",
]

"""Console trace processor — pretty-prints events to stderr."""

from __future__ import annotations

import sys
import threading
from typing import Any

_lock = threading.Lock()


class ConsoleTraceProcessor:
    """Print trace/span lifecycle events to stderr.

    The output is structured but human-readable. Use this for local
    debugging; for production prefer the JSONL exporter.
    """

    def on_trace_start(self, trace: Any) -> None:
        with _lock:
            sys.stderr.write(f"[trace] ▶ {trace.name} ({trace.trace_id})\n")
            sys.stderr.flush()

    def on_trace_end(self, trace: Any) -> None:
        with _lock:
            duration = (trace._ended_at - trace._started_at) if trace._started_at else 0.0
            sys.stderr.write(
                f"[trace] ■ {trace.name} ({trace.trace_id}) {duration * 1000:.1f}ms\n"
            )
            sys.stderr.flush()

    def on_span_start(self, span: Any) -> None:
        with _lock:
            indent = "  " if span.parent_id else "    "
            sys.stderr.write(f"{indent}[span] ▶ {span.name} ({span.span_id})\n")
            sys.stderr.flush()

    def on_span_end(self, span: Any) -> None:
        with _lock:
            indent = "  " if span.parent_id else "    "
            duration = (span.ended_at - span.started_at) if span.ended_at else 0.0
            sys.stderr.write(
                f"{indent}[span] ■ {span.name} ({span.span_id}) {duration * 1000:.1f}ms\n"
            )
            sys.stderr.flush()

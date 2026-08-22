"""JSONL trace processor — appends one JSON object per event to a file."""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any


class JSONLTraceProcessor:
    """Append one JSON object per ``Trace``/``Span`` event to a file.

    The file can be tailed by log aggregators (Vector, Fluent Bit, etc.)
    or post-processed into a richer dashboard format.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        # Truncate on open.
        self._path.write_text("")

    def _emit(self, payload: dict[str, Any]) -> None:
        with self._lock, self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload) + "\n")

    def on_trace_start(self, trace: Any) -> None:
        self._emit(
            {
                "type": "trace_start",
                "event_id": uuid.uuid4().hex,
                "timestamp": time.time(),
                "trace_id": trace.trace_id,
                "name": trace.name,
                "group_id": trace.group_id,
                "metadata": trace.metadata,
            }
        )

    def on_trace_end(self, trace: Any) -> None:
        self._emit(
            {
                "type": "trace_end",
                "event_id": uuid.uuid4().hex,
                "timestamp": time.time(),
                "trace_id": trace.trace_id,
                "name": trace.name,
                "duration_ms": (trace._ended_at - trace._started_at) * 1000
                if trace._started_at
                else 0.0,
            }
        )

    def on_span_start(self, span: Any) -> None:
        self._emit(
            {
                "type": "span_start",
                "event_id": uuid.uuid4().hex,
                "timestamp": time.time(),
                "span_id": span.span_id,
                "parent_id": span.parent_id,
                "trace_id": span.trace_id,
                "name": span.name,
                "metadata": span.metadata,
            }
        )

    def on_span_end(self, span: Any) -> None:
        self._emit(
            {
                "type": "span_end",
                "event_id": uuid.uuid4().hex,
                "timestamp": time.time(),
                "span_id": span.span_id,
                "parent_id": span.parent_id,
                "trace_id": span.trace_id,
                "name": span.name,
                "duration_ms": (span.ended_at - span.started_at) * 1000 if span.ended_at else 0.0,
            }
        )

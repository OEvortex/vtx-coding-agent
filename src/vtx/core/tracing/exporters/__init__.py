"""Built-in trace exporters.

The SDK ships two:

* :class:`ConsoleTraceProcessor` — pretty-prints trace and span events to
  stderr as they complete.
* :class:`JSONLTraceProcessor` — appends one JSON object per event to a
  file, suitable for log aggregation.
"""

from vtx.core.tracing.exporters.console import ConsoleTraceProcessor
from vtx.core.tracing.exporters.jsonl import JSONLTraceProcessor

__all__ = ["ConsoleTraceProcessor", "JSONLTraceProcessor"]

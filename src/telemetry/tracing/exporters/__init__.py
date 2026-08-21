"""Built-in trace exporters.

The SDK ships two:

* :class:`ConsoleTraceProcessor` — pretty-prints trace and span events to
  stderr as they complete.
* :class:`JSONLTraceProcessor` — appends one JSON object per event to a
  file, suitable for log aggregation.
"""

from telemetry.tracing.exporters.console import ConsoleTraceProcessor
from telemetry.tracing.exporters.jsonl import JSONLTraceProcessor

__all__ = ["ConsoleTraceProcessor", "JSONLTraceProcessor"]

# vtx.telemetry

Tracing primitives for the agent runtime: a `Trace` / `Span` pair, a process-global chain of `TraceProcessor` sinks, and two exporters that write to stderr or to a JSONL file. The shape mirrors the OpenAI Agents SDK - one trace per workflow run with nested spans - but the sink is an in-process list of processors, not an HTTP backend, so nothing leaves the machine unless a processor you add sends it.

It is a leaf package: standard library only, no imports from the rest of vtx. The default processor chain starts **empty**, so `trace()` and `span()` do almost nothing until you opt in.

## Usage

The whole package in one script: wrap work in `trace`, nest `span` inside it, and attach a processor to receive the four lifecycle callbacks.

```python
import json
import pathlib
import tempfile

from vtx.telemetry import add_trace_processor, span, trace
from vtx.telemetry.exporters import JSONLTraceProcessor

path = pathlib.Path(tempfile.mkdtemp()) / "trace.jsonl"
add_trace_processor(JSONLTraceProcessor(path))

with trace("session:refactor", metadata={"cwd": "."}) as t:
    print(t.trace_id, t.metadata)
    # trace_1480b3a7d28841e58df1f0d11a34318e {'cwd': '.'}
    with span("read-file", path="README.md") as s:
        print(s.span_id, s.trace_id == t.trace_id, s.metadata)
        # span_b6041a7c743b4c379772d4e5 True {'path': 'README.md'}

for line in path.read_text().splitlines():
    print(json.loads(line))
```

That prints one JSON object per event, in start/end order:

```json
{"type": "trace_start", "event_id": "60ee...", "timestamp": 1791006527.65, "trace_id": "trace_1480...", "name": "session:refactor", "group_id": null, "metadata": {"cwd": "."}}
{"type": "span_start", "event_id": "c13f...", "timestamp": 1791006527.65, "span_id": "span_b604...", "parent_id": null, "trace_id": "trace_1480...", "name": "read-file", "metadata": {"path": "README.md"}}
{"type": "span_end", "event_id": "1d96...", "timestamp": 1791006527.65, "span_id": "span_b604...", "parent_id": null, "trace_id": "trace_1480...", "name": "read-file", "duration_ms": 0.097}
{"type": "trace_end", "event_id": "8879...", "timestamp": 1791006527.65, "trace_id": "trace_1480...", "name": "session:refactor", "duration_ms": 0.339}
```

`span_end` and `trace_end` carry `duration_ms`; the `start` events carry a wall-clock `timestamp` instead, so you compute duration once and never trust clocks twice.

## Traces and spans

- `trace(name, *, group_id=None, metadata=None)` returns a `Trace` dataclass and is also a decorator. Entering it sets it as the current trace and restores the parent on exit, so a nested `trace()` becomes a span of its parent without any extra wiring. With no argument the name is `DEFAULT_WORKFLOW_NAME`, `"Agent workflow"`; a non-string argument also falls back to it.
- `span(name, *, span_data=None, **metadata)` is a `@contextmanager` yielding a `Span`. Keyword arguments become the span's `metadata` bag, which is how `span("read-file", path=...)` ends up with `{"path": ...}`.
- `Trace` fields: `name`, `trace_id` (`trace_` plus 32 hex), `group_id`, `metadata`.
- `Span` fields: `name`, `span_id` (`span_` plus 24 hex), `parent_id`, `trace_id`, `span_data`, `started_at`, `ended_at`, `metadata`.

`parent_id` is the enclosing *span* id, not the trace id, and it is `None` for a top-level span - a span directly inside a `trace()` has `trace_id` set and `parent_id` `None`. A span opened outside any trace has both `None`; that is legal and the exporter still records it.

The current trace and current span are held in `contextvars.ContextVar`s and set on enter, restored on exit, so concurrent tasks each see their own nesting:

```python
from vtx.telemetry import current_span, current_trace

current_trace()  # innermost active Trace, or None
current_span()   # innermost active Span, or None
```

## Processors

`TraceProcessor` is a `runtime_checkable` protocol of `on_trace_start`, `on_trace_end`, `on_span_start`, and `on_span_end`. The SDK wraps every dispatch in `contextlib.suppress(Exception)`, so a processor that raises is silently ignored and never breaks the traced code - but it also means your processor must swallow its own failures if you want to know they happened.

- `add_trace_processor(processor)` appends to the chain.
- `set_trace_processors(processors)` replaces it.
- `get_default_processors()` returns a copy of the current chain; it starts as `[]`.

Four methods are the whole contract:

```python
from vtx.telemetry import add_trace_processor


class CountingProcessor:
    def __init__(self):
        self.spans = 0

    def on_trace_start(self, trace): pass
    def on_trace_end(self, trace): pass
    def on_span_start(self, span): self.spans += 1
    def on_span_end(self, span): pass


add_trace_processor(CountingProcessor())
```

## Decorators

`@trace` and `@trace()` work on sync and async functions. `@trace("named")` does **not**: a non-empty string argument returns a `Trace` object, which is not callable, so decorating with it raises `TypeError: 'Trace' object is not callable`. Use the `with` form when you need a name.

```python
from vtx.telemetry import trace


@trace          # trace name = the function's __name__
def my_workflow():
    ...


@trace()        # same
async def async_workflow():
    ...
```

## Turning it off

- `enable_tracing()` and `disable_tracing()` are a process-wide switch (a module global, not a context variable).
- `is_tracing_disabled()` returns `True` when disabled in-process **or** when `VTX_SDK_DISABLE_TRACING` is set to `1`, `true`, `yes`, or `on` (case-insensitive).

When disabled, `trace()` and `span()` still work as context managers and still yield usable objects - `span()` yields a fresh no-op `Span` with a real `span_id` and no timestamps - but no processor is called and no time is recorded. Instrumented call sites never need a guard.

## Exporters

Both live in `vtx.telemetry.exporters`, not the package root.

- `ConsoleTraceProcessor()` writes `[trace]` and `[span]` start/stop lines with millisecond durations to stderr. It is for local debugging and assumes a single trace at a time.
- `JSONLTraceProcessor(path)` appends one JSON object per event to a file. **It truncates the file on construction** (`self._path.write_text("")`) and creates parent directories, so constructing one over an existing log destroys it.

```python
from vtx.telemetry.exporters import ConsoleTraceProcessor, JSONLTraceProcessor
```

## Not here

Metrics, logging, and cost accounting are out of scope; this package records span timing and span metadata only. Deciding what to trace lives at the call sites in `vtx.agent.sdk`. There is no OTLP exporter, no buffering, and no sampling - ship events out by writing a `TraceProcessor`. Traces are in-memory objects and nothing persists them to the session store.
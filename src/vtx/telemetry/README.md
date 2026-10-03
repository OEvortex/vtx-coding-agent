# vtx.telemetry

Tracing primitives: a `Trace`/`Span` pair, a processor chain, and two built-in exporters.

The shape mirrors the OpenAI Agents SDK (a trace plus nested spans) but the sink is an in-process list of `TraceProcessor`s rather than an HTTP backend. The default processor list is **empty** until something adds one, so tracing is inert until you opt in.

This package is a **leaf**: `grep -rhoE 'from vtx\.[a-z_]+' src/vtx/telemetry` yields only `vtx.telemetry`. No dependency on config, the agent loop, providers, or the UI.

Not responsible for:

- **Metrics, logging, or cost accounting.** This package only records span timing and span metadata.
- **Deciding when to trace.** Instrumented call sites live in `vtx.agent.sdk` (and the core SDK path); this package just provides the context managers they use.
- **Trace transport.** There is no OTLP/HTTP exporter here. Ship events out by writing a class with the four `on_*` methods, or use `JSONLTraceProcessor` and let log aggregation do it.
- **Session persistence.** Traces are in-memory objects; nothing writes them to the session store.

## Dependencies

- Imports: standard library only.
- Imported by: `vtx.agent.sdk` (2 files), `vtx.core` (1 file). Note that `vtx.core/__init__.py` mentions `vtx.telemetry` in a docstring but imports nothing from it, so the only real consumer is the agent SDK.

## Public surface

`vtx.telemetry.__all__`.

### Context managers

| Name | Description |
|------|-------------|
| `trace` | Callable: `trace("name")` returns a `Trace`; bare `@trace` or `@trace()` returns a decorator (sync and async both supported). |
| `span` | `@contextmanager span(name, *, span_data=None, **metadata)`. Yields a `Span`; a no-op `Span` is yielded when tracing is disabled. |
| `Trace` | Dataclass top-level container. Fields: `name`, `trace_id` (`trace_<32 hex>`), `group_id`, `metadata`. Entering sets it as the current trace and restores the parent on exit; a nested `Trace` therefore behaves as a span of its parent. |
| `Span` | Dataclass for one operation. Fields: `name`, `span_id` (`span_<24 hex>`), `parent_id`, `trace_id`, `span_data`, `started_at`, `ended_at`, `metadata`. |
| `DEFAULT_WORKFLOW_NAME` | `"Agent workflow"`, the name used by `trace()` with no argument. |

### Accessors

| Name | Description |
|------|-------------|
| `current_trace() -> Trace \| None` | Innermost active trace, or `None`. |
| `current_span() -> Span \| None` | Innermost active span, or `None`. |

### Switches

| Name | Description |
|------|-------------|
| `enable_tracing()` / `disable_tracing()` | Process-global toggle. |
| `is_tracing_disabled() -> bool` | True when disabled in-process **or** when `VTX_SDK_DISABLE_TRACING` is set to `1`, `true`, `yes`, or `on`. |

### Processors

| Name | Description |
|------|-------------|
| `TraceProcessor` | `runtime_checkable` Protocol: `on_trace_start`, `on_trace_end`, `on_span_start`, `on_span_end`. The SDK wraps every call in `contextlib.suppress(Exception)`, so processors must swallow their own failures. |
| `add_trace_processor(processor)` | Append to the default chain. |
| `set_trace_processors(processors)` | Replace the default chain. |
| `get_default_processors() -> list[TraceProcessor]` | Copy of the current chain. |

### Exporters (`vtx.telemetry.exporters`)

| Name | Description |
|------|-------------|
| `ConsoleTraceProcessor` | Writes `[trace] ▶/■` and `[span] ▶/■` lines with millisecond durations to stderr. For local debugging. |
| `JSONLTraceProcessor` | `JSONLTraceProcessor(path)` appends one JSON object per event (`trace_start`, `trace_end`, `span_start`, `span_end`) to a file. **Truncates the file on construction.** |

## Usage

```python
from vtx.telemetry import add_trace_processor, span, trace
from vtx.telemetry.exporters import ConsoleTraceProcessor

add_trace_processor(ConsoleTraceProcessor())

with trace("session:refactor") as t:
    print(t.trace_id)          # trace_<32 hex>
    with span("read-file", path="README.md") as s:
        print(s.span_id, s.metadata)   # span_<24 hex> {"path": "README.md"}
```

As a decorator, on sync or async functions. Note the limitation: only the bare
`@trace` and `@trace()` forms work. `trace("name")` returns a `Trace` object, which
is not callable, so `@trace("named")` raises `TypeError`. To name a decorated
function, rename the function.

```python
from vtx.telemetry import trace

@trace            # trace name = "my_workflow"
def my_workflow():
    ...

@trace()
async def async_workflow():
    ...
```

Writing a custom sink (the four methods are the whole contract):

```python
from vtx.telemetry import TraceProcessor

class CountingProcessor:
    def __init__(self):
        self.spans = 0
    def on_trace_start(self, trace): pass
    def on_trace_end(self, trace): pass
    def on_span_start(self, span): self.spans += 1
    def on_span_end(self, span): pass

add_trace_processor(CountingProcessor())
```
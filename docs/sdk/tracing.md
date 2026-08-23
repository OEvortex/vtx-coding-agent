# SDK Tracing

Tracing records a tree of spans for a run and ships them to processors. Backed by `core.tracing`; the SDK re-exports the API.

## Instrumenting code

```python
from vtx.ai.agent.sdk import trace, span, current_trace

with trace("support-bot", group_id="user-42"):
    with span("retrieve_order", order_id="123"):
        ...
    # spans nest; current_span()/current_trace() read ambient context
```

`trace` also works as a decorator on functions. Every `Runner.run` creates its own trace automatically unless tracing is disabled.

## Processors

```python
from vtx.ai.agent.sdk import (
    TraceProcessor, ConsoleTraceProcessor, JSONLTraceProcessor,
    add_trace_processor, set_trace_processors,
)

set_trace_processors([JSONLTraceProcessor("runs/trace.jsonl")])   # replace all
add_trace_processor(ConsoleTraceProcessor())                      # append
```

`TraceProcessor` is four callbacks: `on_trace_start/end`, `on_span_start/end`. The JSONL exporter appends one event per callback — point any log pipeline at it.

## Toggles

```python
from vtx.ai.agent.sdk import enable_tracing, disable_tracing
disable_tracing()                          # or RunConfig(tracing_disabled=True)
RunConfig(trace_include_sensitive_data=False)   # keep payloads out of traces
```

Runnable example: [`examples/sdk/07_tracing.py`](../../examples/sdk/07_tracing.py).

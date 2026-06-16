# Tracing

The SDK emits a **trace** for every `Runner.run()` call and **spans**
for each substep (agent start, generation, tool call, handoff,
guardrail). Traces and spans flow through a processor chain.

By default, the processor chain is empty — nothing is collected. Add
one (or more) to capture what you want.

## Built-in processors

```python
from vtx.sdk import add_trace_processor
from vtx.sdk.tracing import ConsoleTraceProcessor, JSONLTraceProcessor

# Pretty-print to stderr.
add_trace_processor(ConsoleTraceProcessor())

# Append JSONL events to a file.
add_trace_processor(JSONLTraceProcessor("/tmp/traces.jsonl"))
```

You can add multiple processors — they all receive every event.
`set_trace_processors([...])` replaces the chain.

## The Trace / Span API

Use `trace` and `span` as context managers or decorators:

```python
from vtx.sdk.tracing import trace, span

# Context manager
with trace("user-onboarding"):
    result = await Runner.run(agent, "Walk me through onboarding")

# Decorator (with or without parens)
@trace("my-workflow")
async def my_workflow():
    ...

@trace()
async def another_workflow():
    ...

# Nested spans
with trace("outer"):
    with span("phase-1"):
        ...
        with span("phase-1.a"):
            ...
```

## Disable tracing globally

```python
from vtx.sdk import disable_tracing, enable_tracing

disable_tracing()  # no-op trace/span contexts
```

Or per-run:

```python
result = await Runner.run(
    agent,
    input,
    run_config=RunConfig(tracing_disabled=True),
)
```

## Sensitive data

By default, spans capture LLM inputs/outputs and tool call
arguments/results. To redact:

```python
result = await Runner.run(
    agent,
    input,
    run_config=RunConfig(trace_include_sensitive_data=False),
)
```

Or set the env var `VTX_SDK_TRACE_INCLUDE_SENSITIVE_DATA=false` (or
`=0`) before launch.

## Writing a custom processor

Implement the `TraceProcessor` protocol (5 methods, all
best-effort — the SDK swallows exceptions from processors):

```python
class MyProcessor:
    def on_trace_start(self, trace): ...
    def on_trace_end(self, trace): ...
    def on_span_start(self, span): ...
    def on_span_end(self, span): ...
```

The `Trace` and `Span` dataclasses carry `trace_id`, `span_id`,
`parent_id`, `started_at`/`ended_at`, plus `metadata` and `span_data`
fields you can populate.

"""
07_tracing.py — built-in trace and span primitives.

The SDK emits a ``Trace`` for every ``Runner.run()`` call and ``Span``s
for each substep. Add a processor to capture them. Two built-ins:

* :class:`ConsoleTraceProcessor` — prints to stderr
* :class:`JSONLTraceProcessor` — appends JSONL events to a file
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from vtx.llm.providers.mock import MockProvider
from vtx.sdk import Agent, Runner
from vtx.sdk.tracing import add_trace_processor, span
from vtx.sdk.tracing.exporters import ConsoleTraceProcessor, JSONLTraceProcessor

agent = Agent(
    name="Traced bot", instructions="Reply simply.", provider=MockProvider(scenario="simple_text")
)


async def main() -> None:
    print("== Console trace ==")
    add_trace_processor(ConsoleTraceProcessor())
    result = await Runner.run(agent, "Hello")
    print(f"  Final output: {result.final_output}")
    print()

    print("== JSONL trace ==")
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "trace.jsonl"
        jsonl_proc = JSONLTraceProcessor(path)
        add_trace_processor(jsonl_proc)
        with span("outer-phase", user_id="u-1"):
            await Runner.run(agent, "World")
        print(f"  Trace file: {path}")
        print(f"  Events: {sum(1 for _ in path.open())}")
        # Print the first three events for a quick look.
        with path.open() as f:
            for i, line in enumerate(f):
                if i >= 3:
                    break
                print(f"    {line.rstrip()}")


if __name__ == "__main__":
    asyncio.run(main())

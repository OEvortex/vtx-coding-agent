# VTX Agentic SDK

The VTX Agentic SDK is Vtx's programmatic, multi-agent interface. It exposes the same lean runtime the CLI uses — Pydantic-typed tools, handoffs, guardrails, approvals and pluggable sessions — as a Python API.

Import it from the `ai.agent.sdk` package (55 public exports):

```python
from vtx.ai.agent.sdk import Agent, Runner, tool
```

## Quick start

```python
from vtx.ai.agent.sdk import Agent, Runner, tool

@tool
def get_weather(city: str) -> str:
    """Return the current weather for a city."""
    return f"Sunny in {city}"

agent = Agent(
    name="Weather bot",
    instructions="Be concise.",
    model="gpt-5.5",
    provider={"name": "openai"},   # uses OPENAI_API_KEY from env
    tools=[get_weather],
)

result = Runner.run_sync(agent, "Weather in Tokyo?")
print(result.final_output)
```

The `provider` field accepts:

- a `BaseProvider` instance (full control),
- a dict — built-in providers need `{name, api_key}`; custom ones add `sdk` and `base_url`,
- `None` — fall back to env vars / config.

An offline-runnable version using `ai.providers.mock.MockProvider` lives at [`examples/sdk/01_quickstart.py`](../../examples/sdk/01_quickstart.py), with seven more examples covering handoffs, manager patterns, guardrails, sessions, approvals, tracing and skills in [`examples/sdk/`](../../examples/sdk).

## Doc map

| Doc | Topic |
| --- | --- |
| [runner.md](runner.md) | Running agents: `run`, `run_sync`, `run_streamed` |
| [agents.md](agents.md) | The `Agent` object, instructions, structured output |
| [tools.md](tools.md) | Custom tools via `@tool` |
| [multi_agent.md](multi_agent.md) | Handoffs and agents-as-tools |
| [approvals.md](approvals.md) | Human-in-the-loop interruptions |
| [permissions.md](permissions.md) | Permission policies (`AutoApprove`, allowlists) |
| [guardrails.md](guardrails.md) | Input/output/tool guardrails |
| [sessions.md](sessions.md) | Session memory backends |
| [skills.md](skills.md) | Loading Vtx skills into SDK agents |
| [tracing.md](tracing.md) | Traces, spans, processors |

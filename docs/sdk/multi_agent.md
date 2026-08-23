# SDK Multi-Agent

Two primitives: **handoffs** (the model transfers the conversation to another agent) and **agents-as-tools** (an agent callable as a tool).

## Handoffs

```python
from ai.agent.sdk import Agent, Runner, handoff

booking = Agent(name="booking", instructions="Book flights. Confirm before paying.")
triage = Agent(
    name="triage",
    instructions="Route requests.",
    handoffs=[booking],          # plain Agent -> transfer_to_booking tool
)

await Runner.run(triage, "Get me to Tokyo next Friday")
```

Adding an agent to `handoffs` compiles a `transfer_to_<name>` tool. When the model calls it, the runner switches the active agent and continues with the target's instructions and tools.

### handoff()

Customize name, description, input payload and history filtering:

```python
from pydantic import BaseModel

class BookingPrefs(BaseModel):
    seat: str = "window"
    max_price: float = 500

h = handoff(
    booking,
    tool_name_override="book_flight",
    tool_description_override="Hand off to flight booking",
    input_type=BookingPrefs,          # typed args required for the transfer
    on_handoff=lambda prefs: print("handing off", prefs),
    input_filter=my_filter,           # (HandoffInputData) -> HandoffInputData
)
```

`HandoffInputData` exposes `input_history`, `pre_handoff_items`, `new_items`, `run_context`; return a (possibly modified) copy. Set `nest_handoff_history=True` on `RunConfig` to keep child transcripts nested rather than flattened into the parent history.

## Agents as tools

Keep control in the caller: the sub-agent runs, its final output comes back as a tool result.

```python
summarizer = Agent(name="summarizer", instructions="Summarize in 3 bullets.")

main = Agent(
    name="writer",
    tools=[summarizer.as_tool(tool_name="summarize", max_turns=10)],
)
```

`as_tool(tool_name=None, tool_description=None, max_turns=None, custom_output_extractor=None)` — the extractor maps the sub-run's result to your preferred string.

Full runnable versions: [`examples/sdk/02_multi_agent_handoff.py`](../../examples/sdk/02_multi_agent_handoff.py), [`03_multi_agent_manager.py`](../../examples/sdk/03_multi_agent_manager.py).

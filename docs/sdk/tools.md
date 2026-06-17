# Tools

## `tool` decorator

The primary way to give an agent capabilities:

```python
from vtx.sdk import tool

@tool
def get_weather(city: str) -> str:
    """Return the current weather for a city."""
    return f"Sunny in {city}"
```

The decorator auto-derives a Pydantic model from the type hints,
generates a JSON Schema the LLM can call, and wraps the function in a
Vtx `BaseTool` subclass.

### Override knobs

```python
@tool(
    name="custom_name",
    description="Custom description",
    needs_approval=True,        # human-in-the-loop
    mutating=False,              # read-only (no permission prompt)
    tool_icon="*",
    input_guardrails=[g1],       # tool-level guardrails
    output_guardrails=[g2],
)
def my_tool(...) -> ...:
    ...
```

### Async functions

`@tool` works on `async def` functions too:

```python
@tool
async def fetch(url: str) -> str:
    async with aiohttp.ClientSession() as s:
        async with s.get(url) as r:
            return await r.text()
```

### Returning `ToolResult`

Tools can return a `vtx.core.types.ToolResult` directly to control
`ui_summary`, `ui_details`, and `is_error`:

```python
from vtx.core.types import ToolResult

@tool
def risky() -> ToolResult:
    return ToolResult(
        success=True,
        result="(internal: did the thing)",
        ui_summary="did the thing",
    )
```

## Manual `BaseTool` subclass

For full control, subclass `vtx.tools.base.BaseTool` directly:

```python
from pydantic import BaseModel
from vtx.core.types import ToolResult
from vtx.tools.base import BaseTool

class MyParams(BaseModel):
    query: str

class MyTool(BaseTool):
    name = "my_tool"
    description = "What this tool does."
    params = MyParams
    mutating = True
    tool_icon = "→"
    # Optional: ship a custom TUI block for this tool. The block must
    # be a subclass of ``vtx.ui.blocks.ToolBlock``; the chat log
    # instantiates it instead of the default block when the LLM
    # invokes the tool. See ``docs/extensions.md#custom-tui-rendering``.
    ui_block = None

    async def execute(self, params, cancel_event=None) -> ToolResult:
        return ToolResult(success=True, result=...)

agent = Agent(name="Bot", tools=[MyTool()])
```

## Tools that wrap an Agent

Use `agent.as_tool(...)` to expose an agent as a callable tool:

```python
sub = Agent(name="Specialist", instructions="x", model="gpt-4o-mini")
parent = Agent(name="Manager", tools=[sub.as_tool()])
```

See [`agents.md`](agents.md) and [`multi_agent.md`](multi_agent.md) for
the difference between **agents-as-tools** and **handoffs**.

## The `Task` tool (TUI subagents)

The TUI ships a built-in `Task` tool that mirrors Claude Code's
`Task`. It dispatches an isolated sub-agent in its own session and
returns the sub-agent's final output as the tool result. See
[`multi_agent.md`](multi_agent.md#tui-the-task-tool) for the full
contract and the built-in `subagent_type` presets.

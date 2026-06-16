# Tools

## `function_tool` decorator

The primary way to give an agent capabilities:

```python
from vtx.sdk import function_tool

@function_tool
def get_weather(city: str) -> str:
    """Return the current weather for a city."""
    return f"Sunny in {city}"
```

The decorator auto-derives a Pydantic model from the type hints,
generates a JSON Schema the LLM can call, and wraps the function in a
Vtx `BaseTool` subclass.

### Override knobs

```python
@function_tool(
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

`@function_tool` works on `async def` functions too:

```python
@function_tool
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

@function_tool
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

# SDK Custom Tools

Turn any function into a tool with the `@tool` decorator. Parameters come from type hints (Pydantic-validated); descriptions from the docstring.

```python
from ai.agent.sdk import tool

@tool
def get_weather(city: str) -> str:
    """Return the current weather for a city.

    Args:
        city: City name, e.g. "Tokyo".
    """
    return f"Sunny in {city}"
```

## Decorator options

```python
@tool(
    name="search_docs",           # defaults to the function name
    description="...",            # defaults to the docstring summary
    needs_approval=False,         # pause for human approval — see approvals.md
    mutating=True,                # False = skip permission prompts entirely
    tool_icon="→",                # TUI/trace badge
    input_guardrails=[...],       # see guardrails.md
    output_guardrails=[...],
)
```

## Rules

- **Type hints are the schema.** `str`, `int`, `float`, `bool`, `list[...]`, `Optional[...]`, enums and nested Pydantic models all map to JSON Schema. Required = no default.
- **Docstrings feed the model.** Both Google style (`Args:` sections) and reST (`:param x:`) are parsed into per-parameter descriptions.
- Return a string (or anything JSON-serializable); raising marks the tool result as an error for the model to react to.
- Sync or async functions both work.

## FunctionTool

The decorator returns a `FunctionTool` (a `BaseTool`). Inspect or reuse it programmatically:

```python
get_weather.name                 # "get_weather"
get_weather.format_call(params)  # pretty call preview
await get_weather.execute(params, cancel_event=None)
```

Agents-as-tools (`agent.as_tool(...)`) also produce `FunctionTool`s — see [multi_agent.md](multi_agent.md).

# SDK Agents

`Agent` bundles everything needed to run one role.

```python
from vtx.ai.agent.sdk import Agent

agent = Agent(
    name="triage",
    instructions="Route requests. Be terse.",
    model="gpt-5.5",
    provider={"name": "openai"},
)
```

## Fields

| Field | Default | Notes |
| --- | --- | --- |
| `name` | required | Also used in handoff tool names |
| `instructions` | `None` | System prompt text, or a callable `(context) -> str` evaluated per run |
| `model` | `None` | Model ID; provider may supply a default |
| `provider` | `None` | `BaseProvider`, dict (`{name, sdk?, api_key?, base_url?, model?, max_tokens?, temperature?, thinking_level?}`), or `None` |
| `tools` | `[]` | `@tool` functions, `BaseTool`s |
| `handoffs` | `[]` | Target agents / `handoff()` wrappers (see [multi_agent.md](multi_agent.md)) |
| `output_type` | `None` | Pydantic model → validated `final_output` (below) |
| `input_guardrails` / `output_guardrails` | `[]` | See [guardrails.md](guardrails.md) |
| `tool_use_behavior` | `"run_llm_again"` | Feed tool results back to the model |
| `needs_approval_tools` | `set()` | Tool names requiring approval — see [approvals.md](approvals.md) |
| `metadata` | `{}` | Free-form |

## Structured output

```python
from pydantic import BaseModel
from vtx.ai.agent.sdk import Agent, AgentOutputSchema

class Report(BaseModel):
    summary: str
    risks: list[str]

agent = Agent(name="auditor", output_type=Report, ...)
# final_output is a Report instance.
```

`AgentOutputSchema(output_type=..., validate_strict=False)` wraps the schema when you need the raw JSON schema or manual validation.

## Handy methods

```python
agent.clone(model="gpt-5.5")                 # copy with overrides
agent.as_tool(tool_name="audit_repo")        # wrap this agent as a tool
agent.resolve_instructions(context)          # str or callable -> str
agent.build_system_prompt(tools=[...])       # full lean vtx-style prompt
agent.all_tools()                            # tools + handoff tools compiled
```

Providers are resolved lazily: dicts go through the Vtx catalog (57 built-ins), so `{name: "ollama"}` just works for local models.

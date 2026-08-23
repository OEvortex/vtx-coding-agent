# SDK Guardrails

Guardrails run checks around the model and tools; raising a tripwire aborts the run. Four decorators cover the four seams:

| Decorator | Runs | Data |
| --- | --- | --- |
| `@input_guardrail` | Before the first model call of a run | `context`, `agent`, `input` |
| `@output_guardrail` | On each final output | `context`, `agent`, `output` |
| `@tool_input_guardrail` | Before a tool executes (arguments as JSON string) | `context`, `tool_name`, `tool_arguments` |
| `@tool_output_guardrail` | After a tool returns | `context`, `tool_name`, `tool_result` |

```python
from vtx.ai.agent.sdk import input_guardrail, tool_output_guardrail
from vtx.ai.agent.sdk.guardrails.types import GuardrailFunctionOutput, ToolGuardrailFunctionOutput

@input_guardrail
def no_secrets(ctx, agent, inp):
    if "sk-" in str(inp):
        return GuardrailFunctionOutput(output_info="api key in input", tripwire_triggered=True)
    return GuardrailFunctionOutput(tripwire_triggered=False)

@tool_output_guardrail
def redact_tokens(ctx, tool_name, tool_result):
    return ToolGuardrailFunctionOutput.reject_content("[redacted]")

agent = Agent(name="safe", input_guardrails=[no_secrets], ...)
```

Tool-output guardrails have three constructors: `.allow()`, `.reject_content(message)` (replace the result), `.raise_exception()`.

## Tripwires

A triggered guardrail raises — and the runner surfaces — typed exceptions:

- `InputGuardrailTripwireTriggered` / `OutputGuardrailTripwireTriggered`
- `ToolInputGuardrailTripwireTriggered` / `ToolOutputGuardrailTripwireTriggered`

Each carries the guardrail name and `output_info`.

Runnable example: [`examples/sdk/04_guardrails.py`](../../examples/sdk/04_guardrails.py).

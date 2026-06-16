# Runner

`Runner` is the single entry point for executing agents. It owns the
agentic loop, applies guardrails, manages sessions, and emits a typed
event stream.

## Variants

| Method | Async? | Use when |
|---|---|---|
| `Runner.run(agent, input, ...)` | yes | The canonical entry point. |
| `Runner.run_sync(agent, input, ...)` | no | Sync context. Runs in a worker thread. |
| `Runner.run_streamed(agent, input, ...)` | yes | Iterate events as they arrive. |

```python
result = await Runner.run(agent, "hello")
result = Runner.run_sync(agent, "hello")
streamed = Runner.run_streamed(agent, "hello")
async for event in streamed:
    ...
result = streamed.result
```

## Inputs

`input` can be a string or a list of input-item dicts (in the same
format the `Session` Protocol uses).

```python
result = await Runner.run(
    agent,
    [
        {"role": "user", "content": "Hello"},
        {"role": "user", "content": "Are you there?"},
    ],
)
```

## RunConfig

`RunConfig` holds per-run knobs:

```python
from vtx.sdk import RunConfig

result = await Runner.run(
    agent,
    input,
    run_config=RunConfig(
        max_turns=10,
        session_input_callback=my_callback,
        tracing_disabled=False,
        permission_policy=my_policy,
    ),
)
```

| Field | Description |
|---|---|
| `max_turns` | Maximum model turns before the run stops. |
| `session_input_callback` | `(history, new_input) -> final_input`. Customize how the session's stored history is merged with the new turn's input. |
| `tracing_disabled` | Skip the default trace for this run. |
| `trace_include_sensitive_data` | If False, LLM/tool inputs/outputs are not captured in spans. |
| `permission_policy` | Override the SDK's default `PermissionPolicy`. |
| `nest_handoff_history` | Opt-in: collapse the prior transcript into a single summary block when a handoff occurs. |
| `custom` | Free-form dict for app-side bookkeeping. |

## RunResult

Every run returns a `RunResult`:

```python
@dataclass
class RunResult:
    final_output: Any        # str, or output_type instance
    new_items: list[RunItem] # typed items generated during the run
    interruptions: list      # ToolApprovalItems that need a decision
    state: RunState | None   # Resumable state, populated on pause
    stop_reason: StopReason
    usage: Usage
    agent_name: str
```

`result.to_input_list()` returns the conversation as a list of
input-item dicts, ready to feed back into another run.

## Stop reasons

`StopReason.STOP` — natural end. `TOOL_USE` — last turn called a tool
(only meaningful inside the loop). `LENGTH` — max turns hit.
`ERROR` — model error. `INTERRUPTED` — user cancel. `STEER` — steer
event fired.

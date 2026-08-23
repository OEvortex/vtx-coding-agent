# SDK Runner

`Runner` executes an agent to completion. All three methods share the same parameters:

```python
from vtx.ai.agent.sdk import Agent, Runner, RunConfig

result = await Runner.run(agent, "Draft a release note", session=ses, run_config=cfg)
result = Runner.run_sync(agent, "...")            # sync wrapper
streamed = Runner.run_streamed(agent, "...")      # returns immediately
```

## Parameters

| Param | Notes |
| --- | --- |
| `starting_agent` | The `Agent` to run |
| `input` | A `str`, or a list of input items (previous conversation) |
| `session` | Optional session backend (see [sessions.md](sessions.md)); history is merged with input |
| `run_config` | A `RunConfig` (below) |
| `max_turns` | Per-run override of the turn budget |
| `cancellation` | `asyncio.Event` to cancel cooperatively |
| `context` | Opaque object passed to instructions callables, guardrails and tools |

## RunConfig

| Field | Default | Notes |
| --- | --- | --- |
| `max_turns` | `None` | Global default turn budget |
| `permission_policy` | `None` | See [permissions.md](permissions.md) |
| `tracing_disabled` | `False` | Turn off tracing for this run |
| `trace_include_sensitive_data` | `True` | Include payloads in traces |
| `session_input_callback` | `None` | Filter/merge history before each turn |
| `session_settings` | `None` | `SessionSettings(limit=…)` |
| `nest_handoff_history` | `False` | Keep handoff child transcripts nested in the parent history |
| `custom` | `{}` | Free-form bag |

## Streaming

`run_streamed` returns a `RunStreamed`. Iterate it for live events (`_TextDelta`, `_ToolCallStart`, `_ToolCallEnd`, `_ToolResult`, `_AgentStartEvent`); after exhaustion, `.result` yields the final `RunResult`.

## RunResult

```python
result.final_output        # last agent text, or validated output_type instance
result.new_items           # list[RunItem]: messages, tool calls/results, handoffs
result.interruptions       # list[ToolApprovalItem] when needs_approval paused
result.state               # RunState — pass to approve()/reject() and re-run
result.stop_reason         # stop | length | tool_use | error | interrupted | steer
result.usage               # accumulated Usage
result.agent_name          # agent that produced the final output
result.to_input_list()     # items as plain dicts, feed back as input
```

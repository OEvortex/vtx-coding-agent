# Multi-agent: handoffs vs. agents-as-tools

The SDK has two patterns for multi-agent collaboration. They have
different ownership semantics — pick by what you want the parent to
control.

## Agents-as-tools (manager pattern)

The parent **owns** the conversation. The sub-agent's output is one
data point among many; the parent can combine it with other tool
results before replying.

```python
sub = Agent(
    name="Specialist",
    instructions="You answer questions about refunds.",
    model="gpt-4o-mini",
)
parent = Agent(
    name="Coordinator",
    instructions="Help the customer. Use your tools when relevant.",
    model="gpt-4o-mini",
    tools=[
        sub.as_tool(tool_name="ask_refund_specialist"),
    ],
)
```

When the parent calls `ask_refund_specialist`, the sub-agent runs
synchronously, returns its `final_output` as the tool's result, and
the parent continues.

## Handoffs

The sub-agent **takes over** the conversation. The handoff tool call
transfers the active agent; the rest of the turn (and possibly the
rest of the run) is owned by the target.

```python
from vtx.sdk import handoff

refund_agent = Agent(
    name="Refund",
    instructions="You handle refund questions end-to-end.",
    model="gpt-4o-mini",
)
triage = Agent(
    name="Triage",
    instructions="Route to the right specialist.",
    model="gpt-4o-mini",
    handoffs=[
        handoff(
            refund_agent,
            tool_description_override="Hand off refund questions to the Refund agent.",
        ),
    ],
)
```

The triage agent gets a `transfer_to_refund` tool. When the model
calls it, the SDK switches the active agent to `Refund` for the
remainder of the run.

### Handoff options

```python
handoff(
    target_agent,
    tool_name_override="ask_refund",
    tool_description_override="...",
    on_handoff=my_callback,           # sync or async
    input_filter=my_filter,           # HandoffInputData -> HandoffInputData
    input_type=HandoffData,           # structured input the LLM can pass
)
```

| Option | Description |
|---|---|
| `tool_name_override` | Override the auto-generated `transfer_to_<agent>` name. |
| `tool_description_override` | Override the auto-generated description. |
| `on_handoff` | Sync or async callback fired the moment the handoff is invoked. |
| `input_filter` | A function that transforms the conversation history before handing it to the target. |
| `input_type` | Pydantic model for structured input the LLM can pass. |

## Which to use?

| Use **agents-as-tools** when | Use **handoffs** when |
|---|---|
| The specialist's reply is one data point among many. | The specialist should own the next part of the conversation. |
| You want one agent to combine outputs from multiple specialists. | Routing itself is the workflow; you don't want the manager narrating the result. |
| You want shared guardrails in one place. | You want focused prompts and tools per specialist. |
| You want explicit tracing of "manager invoked X" in the trace. | You want clean delegation: the trace shows the target agent's run. |

You can also combine them. A triage agent can hand off to a
specialist, and the specialist can in turn call other agents as tools.

## TUI: the `Task` tool

The `Task` tool is a default built-in tool in `vtx` that lets the LLM delegate well-scoped work to an isolated sub-agent (similar to Claude Code's `Task` tool). The LLM will have a `task` tool available with the surface below.

It reads the generic [`vtx.dispatcher`](../README.md) slot to get the parent's runtime context (provider, model, cwd, agent-registry). The dispatcher slot is a vtx platform feature.

```python
Task(
    description="Find the auth bug",     # 3-5 word label
    prompt="...",                        # the actual instructions
    subagent_type="Explore",             # optional: preset or .vtx/agent/ name
    model="...",                         # optional override
)
```

Built-in `subagent_type` presets:

- `general-purpose` — balanced, all read-only tools plus the
  full default toolset.
- `Explore` — read-only repo navigation (`read`, `find`,
  `web_search`, `fetch_webpage`).
- `Plan` — read-only, instructions tuned for producing a plan.

User-defined agents from `.vtx/agent/<name>.py` are also accepted by
name. Sub-agent sessions are persisted under
`~/.vtx/tasks/<safe_cwd>/`, isolated from the parent's session.

### The sub-agent → main agent contract

The `Task` tool returns **only the sub-agent's final text** to the
main agent — no preamble, no transcript of tool calls, no
"sub-agent made N tool calls" framing, no truncation markers. The
sub-agent's system prompt is augmented with a directive that tells
it to give a focused, self-contained answer.

The full transcript (turns, tool calls, token usage, session id,
model name) is preserved in `ui_details` for the TUI only — the
LLM never sees it. If you need to debug a sub-agent after the fact,
its full session is at `~/.vtx/tasks/<safe_cwd>/<id>.jsonl`.

Live progress (text + tool calls) streams into a nested label under
the parent `Task` block in the TUI; the final text is returned as
the tool result. v1 is synchronous; background/parallel sub-agents
are on the roadmap.

For the SDK-level patterns above, see the rest of this document.
For the TUI tool specifically, see [the changangelog](../../CHANGELOG.md)
or the `TaskTool` source in `src/vtx/tools/task.py`.

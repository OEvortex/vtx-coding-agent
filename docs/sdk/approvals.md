# SDK Approvals

Pause a run when specific tools are called and decide from your own UI. Implemented with `needs_approval_tools` + run interruptions.

```python
from ai.agent.sdk import Agent, Runner, ToolApprovalItem

agent = Agent(
    name="ops",
    instructions="Manage deploys.",
    tools=[deploy, rollback],
    needs_approval_tools={"deploy"},     # only these pause
)

result = await Runner.run(agent, "Ship v2 to prod")

if result.interruptions:
    state = result.state                 # RunState: original input + pending calls
    for item in result.interruptions:    # ToolApprovalItem
        print(item.tool_name, item.arguments)
        state.approve(item)              # or state.reject(item)

# Re-run the original input; recorded decisions gate the pending calls.
result = await Runner.run(agent, result.state.original_input)
```

## Pieces

| Object | Role |
| --- | --- |
| `ToolApprovalItem` | One paused tool call: `tool_name`, `arguments`, `call_id`, `name` |
| `RunState` | Carries `original_input`, pending calls and decisions; `.approve(*items)`, `.reject(*items)`, `.decision_for(call_id)` |
| `ApprovalDecision` | `approve` / `reject` enum |

Rejected calls return a "denied by user" tool result so the model can pick another path.

## When you just want a policy, not a UI

Skip interruptions entirely with a `PermissionPolicy` — see [permissions.md](permissions.md). Policies decide synchronously per call; approvals hand the decision to your code between runs.

Runnable example: [`examples/sdk/06_approvals.py`](../../examples/sdk/06_approvals.py).

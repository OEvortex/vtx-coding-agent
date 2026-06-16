# Approvals (human-in-the-loop)

A tool marked `needs_approval=True` will pause the run before
executing. The SDK returns a `RunResult` with `interruptions`
populated; you inspect the pending calls, decide, and resume with a
`RunState`.

## Marking a tool

```python
from vtx.sdk import function_tool

@function_tool(needs_approval=True)
def send_email(to: str, subject: str, body: str) -> str:
    """Send an email on the user's behalf."""
    return f"sent to {to}"
```

## Handling the pause

```python
from vtx.sdk import ApprovalDecision, Runner

result = await Runner.run(agent, "Send the report to alice@example.com")

if result.interruptions:
    state = result.state
    for item in result.interruptions:
        # Inspect item.tool_name, item.arguments
        # Then approve or reject
        state.approve(item)   # or: state.reject(item)

    # Resume the same run
    result = await Runner.run(agent, state)
```

The resumed run continues exactly where it left off. Pending
rejections produce a `tool_result` containing the rejection reason
that the LLM can read and act on.

## The state object

`RunState` is a serializable snapshot of the run:

```python
@dataclass
class RunState:
    original_input: Any
    pending_tool_calls: list[ToolCall]
    decisions: list[_PendingDecision]
    new_items: list[RunItem]
    metadata: dict[str, Any]
```

You can serialize it (e.g. with `dataclasses.asdict`) and resume
later in a different process — useful for long-running workflows
where a human might take minutes or hours to approve.

## Permissions (Vtx-specific)

Approvals coexist with Vtx's permission system. The flow is:

1. The SDK calls your `PermissionPolicy.decide(tool, args)`.
2. If the policy returns `ALLOW`, the tool runs.
3. If the policy returns `PROMPT` and the tool has
   `needs_approval=True`, the run pauses.
4. If the policy returns `PROMPT` and the tool does NOT have
   `needs_approval=True`, the tool is rejected (no pause) — same as
   Vtx's headless mode behavior.

See [`permissions.md`](permissions.md) for the policy primitives.

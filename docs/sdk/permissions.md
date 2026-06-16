# Permissions

Vtx has a permission system. The SDK exposes it via the
`PermissionPolicy` Protocol.

## Default policy

`PromptApprove` (the SDK default) mirrors Vtx's
`check_permission` semantics:

- `mutating=False` tools are auto-approved.
- For mutating tools, the policy returns `PROMPT`.
- For `bash`, safe read-only commands (e.g. `ls`, `cat`) are
  auto-approved.

When the policy returns `PROMPT`, the SDK asks the host app for a
decision. The host app supplies a `PermissionCallback` (or implements
its own `PermissionPolicy`).

## Built-in policies

```python
from vtx.sdk import AutoApprove, AllowlistApprove, PromptApprove

# Allow everything. Mirrors `permissions.mode=auto`.
policy = AutoApprove()

# Allow only listed tools; prompt for everything else.
policy = AllowlistApprove(["web_search", "fetch_webpage"])

# Default: read-only auto-approved, mutating prompts.
policy = PromptApprove()
```

## Custom policy

```python
from vtx.sdk import PermissionDecision, PermissionPolicy
from vtx.tools.base import BaseTool

class WorkspaceOnlyPolicy(PermissionPolicy):
    def decide(self, tool: BaseTool, arguments: dict) -> PermissionDecision:
        path = arguments.get("path", "")
        if not str(path).startswith("/workspace/"):
            return PermissionDecision.PROMPT
        return PermissionDecision.ALLOW

agent = Agent(
    name="Bot",
    permission_policy=WorkspaceOnlyPolicy(),
    ...
)
```

Async policies are also supported:

```python
class AsyncPolicy(PermissionPolicy):
    async def decide(self, tool, arguments):
        # ask a remote service
        return PermissionDecision.ALLOW
```

## Per-run override

```python
result = await Runner.run(
    agent,
    input,
    run_config=RunConfig(permission_policy=my_policy),
)
```

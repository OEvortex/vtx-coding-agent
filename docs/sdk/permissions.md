# SDK Permissions

A `PermissionPolicy` decides allow/prompt per tool call before it executes — the programmatic equivalent of Vtx's `prompt`/`auto` modes.

```python
from vtx.ai.agent.sdk import Agent, AutoApprove, AllowlistApprove, PromptApprove, RunConfig

run_config = RunConfig(permission_policy=AutoApprove())          # run everything
run_config = RunConfig(permission_policy=AllowlistApprove(["read", "find", "web"]))
```

## Built-in policies

| Policy | Behaviour |
| --- | --- |
| `AutoApprove` | Allow everything. |
| `AllowlistApprove(allowlist)` | Tools named in the list → `ALLOW`; everything else → `PROMPT`. |
| `PromptApprove` | Read-only bash commands (its built-in safe list: `cat head tail ls pwd wc diff which file stat du df whoami id uname date realpath dirname basename`) auto-approve; other mutating calls prompt. |

`PROMPT` without an approval flow surfaces as a run interruption (`ToolApprovalItem`) — combine with [approvals.md](approvals.md) to resolve it.

## Custom policies

Subclass and decide yourself; sync or async both work:

```python
from vtx.ai.agent.sdk import PermissionPolicy

class BusinessHours(PermissionPolicy):
    def decide(self, tool, arguments):
        if tool.name == "deploy" and after_hours():
            return "prompt"
        return "allow"
```

Prefer a plain callback?

```python
from vtx.ai.agent.sdk import Agent, Runner
from vtx.ai.agent.sdk.permissions import PermissionCallback

policy = PermissionCallback(lambda tool, args: "deny" if tool.name == "bash" else "allow")
```

Return values: `"allow"` / `"prompt"` (or `PermissionDecision` members).

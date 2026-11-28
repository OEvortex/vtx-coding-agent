# Permissions

How vx decides whether a tool call runs. The modes and rules live in `packages/vx/src/vx/permissions.py`; `/mode` and `/permissions` switch between them.

## Modes

`BUILTIN_MODES` defines two:

| Mode | Behaviour |
|---|---|
| `ask` (display name *Ask*) | Every mutating tool call prompts for approval before it runs |
| `auto` (*Auto*) | Bypass all checks — everything runs freely |

`DEFAULT_MODE` is `auto`. `/mode` with no argument opens a picker over the built-in modes; `/permissions <mode>` switches by id, with `prompt` accepted as an alias for `ask`. `/mode` also syncs the ctx config so the hint row agrees.

## Session grants

A `PermissionGrant` records "this tool may touch this path for the rest of the session", so approving once does not re-ask for every edit in the same directory.

## Read-only tools

`READ_ONLY_TOOLS` — `read`, `grep`, `web`, `web_search`, `web_fetch` — never mutate state and are always allowed without a prompt, even in `ask` mode.

## Rule decisions

Each tool call is checked against the mode and the session's grants and yields one of `RuleDecision.allow`, `.deny`, or `.permission_required`; a `permission_required` result becomes the approval prompt (or, on the swarm board, a `needs you` card — see [swarm.md](swarm.md)).

## Non-interactive runs

`vx -p` has no dialog to approve with, so every `ToolApprovalEvent` is answered DENY and `AskUserEvent` gets an empty response — see the README's headless section for exit codes. There is no bypass flag; run headless with the permissions you intend.

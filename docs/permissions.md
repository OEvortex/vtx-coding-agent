# Permissions

Vtx gates mutating tool calls by default. Everything here is implemented in `src/core/permissions.py` and wired into the turn loop in `src/ai/agent/turn.py`.

## Modes

| Mode | Behaviour |
| --- | --- |
| `prompt` (default) | Non-mutating tools run freely; **mutating** tools pause for approval. |
| `auto` | Nothing pauses; the agent runs unattended. |

Toggle live with `alt+ctrl+p` or `/settings` → permissions. Set the default under `permissions.mode` in config.

## The decision algorithm

For each tool call:

1. Extension/agent permission gates are consulted first (see below). A `deny` blocks, an `allow` short-circuits.
2. If the tool is non-mutating (`read`, `find`, `grep`, `web`, `ask_user`, `task`) → **allow**.
3. `bash`: the command is parsed and checked against the safe lists (below). A read-only command with no shell punctuation → **allow**; anything else → **prompt**.
4. Every other mutating tool (`edit`, `write`, `skill`) → **prompt**.

In `auto` mode steps 3–4 allow without asking.

## Safe command lists

`bash` commands auto-approve only when every pipeline segment starts with a known read-only binary and the command contains none of `; | & ( ) < >`:

Safe binaries: `cat head tail ls pwd wc diff which file stat du df whoami id uname date realpath dirname basename`

Safe git subcommands: `status diff log show rev-parse describe ls-files ls-tree blame shortlog`

Examples: `git diff` is allowed; `git diff && git push` prompts (punctuation); `rm -rf /` prompts — and destructive commands (`rm -rf`, `git reset --hard`, force-push) are always surfaced for explicit confirmation.

## The approval UI

A pending mutation renders inline with a preview of what will happen. Choose approve or deny with the keyboard; `escape` denies everything pending. Denials return "Interrupted/denied" as the tool result so the model can adjust.

## Agent & extension gates

Handoff agents can ship declarative gates (`permission_gates` on `AgentDef`):

```python
api.permission_gate(
    "bash",
    when=lambda args: "push" in args.get("command", ""),
    action="deny",          # "allow" | "deny" | "prompt"
    reason="No pushes from this agent",
)
```

Extensions register the same thing through the bus. Gates evaluate before the mode check, so an `allow` gate can whitelist a specific mutation even in `prompt` mode.

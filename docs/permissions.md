# Permissions

Vtx gates mutating actions behind a permission system so the agent cannot silently change your files or run commands. The two mutating tools are `bash`, `edit`, and `write`; destructive commands are additionally blocked unless explicitly requested.

## Modes

Set in `config.yml` under `permissions.mode`:

```yaml
permissions:
  mode: "prompt"   # or "auto"
```

- **`prompt`** (default) — the TUI prompts you to approve every mutating tool call before it runs. You can allow once, allow for the session, or deny.
- **`auto`** — mutating tools run without confirmation. Use only in trusted, sandboxed contexts.

Toggle the mode at runtime in the TUI with the `/permissions` slash command or `Shift+Tab`.

## How approvals work

When a mutating tool call is proposed in `prompt` mode, Vtx raises an approval request. The decision flow (`src/vtx/permissions.py`):

- `PermissionDecision.ALLOW` — the call proceeds.
- `PermissionDecision.PROMPT` — the UI asks the user. The user responds with `ApprovalResponse.APPROVE` or `ApprovalResponse.DENY`.

A denied or dismissed (Escape) approval cancels that tool call; the model is told the call was not permitted and can adjust its approach.

## Safety guarantees

Regardless of permission mode, Vtx refuses destructive operations unless you explicitly ask for them, e.g.:

- `rm -rf`, forceful recursive deletes
- `git reset --hard`, `git checkout` of branches
- force-push (`git push --force`)
- dropping database tables

Interactive, blocking commands (`vim`, `less`, `top`) are also avoided so the loop never hangs.

## Hook-based gating

Extensions can intercept and pre-approve or veto tool calls via the hook lifecycle — see [extensions.md](extensions.md) and [hooks.md](hooks.md).

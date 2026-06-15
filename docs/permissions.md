# Permissions

Vtx has a two-mode permission system. The default (`prompt`) asks the user before any mutating tool call; the alternative (`auto`) skips the prompt and trusts the agent.

This doc covers the modes, the safe-command allowlist, and the exact decision algorithm.

## Modes

| Mode | Behavior | CLI override | Slash command |
| --- | --- | --- | --- |
| `prompt` (default) | Ask before each mutating call. Read-only tools and clearly read-only shell commands run without prompting. | — | `/permissions prompt` |
| `auto` | Skip all approval prompts. Mutating tools run as soon as the model calls them. | — | `/permissions auto` |

The mode is persisted in `~/.vtx/config.yml` under `permissions.mode`. `/permissions` shows a picker; selecting an option writes the config and updates the in-memory state. You can also `Shift+Tab` to cycle through modes inside a session.

The info bar in the TUI shows the current mode:

- `✓✓ auto` — auto mode is active (the `✓✓` is the "go" symbol)
- `⏸ prompt` — prompt mode is active (the `⏸` is the "wait" symbol)

## The decision algorithm

The full decision is in [`src/vtx/permissions.py`](../src/vtx/permissions.py). In plain English:

```text
1. If permissions.mode is "auto": ALLOW.
2. If the tool is not mutating (read/find/web_*): ALLOW.
3. If the tool is "bash" and the command is on the safe-command allowlist: ALLOW.
4. Otherwise: PROMPT.
```

That's it. Step 3 is the only interesting bit — it lets you run a long series of read-only shell commands without 20 popups.

## Safe-command allowlist

A bash command is auto-allowed in `prompt` mode iff:

- The command parses cleanly with POSIX `shlex` (no syntax errors, no embedded `;` / `|` / `&` / `()` / `<>` / `<(...)` / `>(...)` as a free-standing token, no newlines).
- The base command (after stripping the path prefix) is in the allowlist below.
- If the base is `git`, the subcommand is in the read-only git allowlist AND the command does not include `--output` (which writes diff to a file).

### Read-only commands

`cat`, `head`, `tail`, `ls`, `pwd`, `wc`, `diff`, `which`, `file`, `stat`, `du`, `df`, `whoami`, `id`, `uname`, `date`, `realpath`, `dirname`, `basename`.

### Read-only git subcommands

`status`, `diff`, `log`, `show`, `rev-parse`, `describe`, `ls-files`, `ls-tree`, `blame`, `shortlog`.

`git -C <path> ...` and `git --git-dir <path> ...` are honored. `git --output ...` is rejected (writing diff to a file is mutating). `git --config-env ...` and `git -c <var>=<val> ...` are rejected to prevent config-injection attacks.

### What's NOT auto-allowed

- Anything that writes: `mv`, `cp`, `rm`, `mkdir`, `touch`, `tee`, `>`, `>>`, `sed -i`, `awk -i`, `pip install`, `npm install`, etc.
- Git write operations: `commit`, `push`, `checkout`, `reset`, `clean`, `merge`, `rebase`, `tag`, `branch -d`, etc.
- Network tools: `curl`, `wget`, `ssh`, `nc`, `ncat`, etc.
- Anything with `&&` or `||` chains — those are still parsed, but only the first command's name is checked. If you write `rm file && echo done`, the `rm` triggers a prompt.

## Approval prompts

When the decision is `PROMPT`, the TUI shows a modal with:

- The tool name and icon.
- The arguments (in the theme's badge color).
- A one-line preview of what the call will do (e.g. the diff for `edit`, the file content for `write`, the command line for `bash`).
- The keyboard choice: `y` approve, `n` deny, plus arrow-key navigation if you want to inspect first.

Denials are sent back to the model as a tool error so the model can adapt. Approvals are session-scoped per call — there's no "always allow this command" rule yet (intentional, to keep the model honest).

## Non-interactive mode

In headless mode (`vtx -p "..."`), there's no way to display a prompt. Vtx forces `permissions.mode = "auto"` for the duration of the headless run and restores the prior mode afterward. If the underlying code path still produces a `ToolApprovalEvent` (it shouldn't, but defensively), the headless renderer auto-denies and writes `error: '<tool>' requires approval, denied (non-interactive mode)` to stderr. See [headless.md](headless.md).

## Recommended setup

- **For solo dev work on your own machine:** `auto` is fine. The agent's worst case is the same worst case as a typo in your shell.
- **For shared/important repos:** keep `prompt`. The model will still stream normally for read-only work, and you'll only see popups for the destructive steps you actually care about.
- **For batched/automation runs:** use `vtx -p` (headless). The permission mode is forced to `auto` automatically.

## Verifying behavior

You can audit Vtx's permission decisions from the test suite:

- [`tests/test_local_auth_config.py`](../tests/test_local_auth_config.py) — local auth mode behavior.
- [`tests/ui/test_permissions_command.py`](../tests/ui/test_permissions_command.py) — `/permissions` UI flow.
- [`tests/ui/test_input_approval_submit.py`](../tests/ui/test_input_approval_submit.py) — keyboard submission of approval prompts.

The full safe-command allowlist is exported as `vtx.permissions.SAFE_COMMANDS` and `SAFE_GIT_SUBCOMMANDS`.

# Permissions

Every tool call is either allowed to run or the agent stops and asks you; this page
covers the two modes, the rules that decide which calls need an answer, how `bash`
commands are classified, and how a plugin tightens the gate.

## The two modes

There are exactly two values. They are a `Literal`, not an enum
(`src/vtx/core/config.py:44`):

```python
PermissionMode = Literal["prompt", "auto"]
PERMISSION_MODES: tuple[PermissionMode, ...] = get_args(PermissionMode)
```

| Mode | Question it answers | Default | Where it lives |
|---|---|---|---|
| `prompt` | Ask me before a mutating tool call | yes | `permissions.mode` (`src/vtx/core/config.py:152-153`) |
| `auto` | Run every tool call without asking | no | `permissions.mode` |

The picker spells them out the same way (`src/vtx/tui/commands/settings.py:76-78`):

| Mode | Description in the picker |
|---|---|
| `prompt` | ask before mutating tool calls |
| `auto` | allow tool calls without approval prompts |

`auto` is not a read-only mode and not a safety setting. It allows every call,
including `rm -rf ./build`. It exists for runs where nobody can answer, which is why
headless forces it (below).

**There is no plan mode.** `prompt` and `auto` are the only two values the config
model accepts, the only two `check_permission` ever returns, and the only two the
agent-level `permission_mode` field accepts (`src/vtx/agent/agents/schema.py:75`).
There used to be a hardcoded `plan` agent profile, and the loader records why it was
removed (`src/vtx/agent/agents/loader.py:173-178`):

```text
No built-in profiles. They used to be hardcoded here, which meant every session
carried a `plan` profile the user never asked for, and editing the shipped list was
the only way to change what the agent could be.
```

If you want the agent to stop and agree on a plan before acting, that is a goal with a
verification contract (`goals.md`) or a `permission_gate` on the tool that would act
(`agents.md`), not a permission mode.

## The base decision

One function is the policy (`src/vtx/core/permissions.py:175-194`):

```python
def check_permission(
    tool: BaseTool, arguments: dict, config: object | None = None
) -> PermissionDecision:
    if config is None:
        try:
            from vtx.core.config import config as _config
            config = _config
        except Exception:
            config = None
    permissions = getattr(config, "permissions", None)
    if permissions is not None and permissions.mode == "auto":
        return PermissionDecision.ALLOW
    if not tool.mutating:
        return PermissionDecision.ALLOW
    if tool.name == "bash":
        command = arguments.get("command", "")
        if _is_safe_bash_command(command):
            return PermissionDecision.ALLOW
    return PermissionDecision.PROMPT
```

First match wins, in this order:

| Order | Check | Result |
|---|---|---|
| 1 | `permissions.mode == "auto"` | `ALLOW` |
| 2 | `tool.mutating` is `False` | `ALLOW` |
| 3 | tool is `bash` and the command is in the safe set below | `ALLOW` |
| 4 | anything else | `PROMPT` |

Note what is absent. The base policy has no deny: the only two outcomes are
`PermissionDecision.ALLOW` and `PermissionDecision.PROMPT`
(`src/vtx/core/permissions.py:8-11`). A refusal happens when you decline the prompt,
one layer up in the turn loop. Also note that step 2 runs before the command check, so
in `prompt` mode a non-mutating tool never prompts no matter what its arguments are.

## Which tools are mutating

`BaseTool.mutating` defaults to `True` (`src/vtx/agent/tools/base.py:18`), so a tool
that writes something and forgets the flag is gated. The default set:

| Tool | `mutating` | Prompts in `prompt` mode | Source |
|---|---|---|---|
| `read` | `False` | never | `src/vtx/coding_agent/tools/read.py:34` |
| `find` | `False` | never | `src/vtx/coding_agent/tools/find.py:34` |
| `grep` | `False` | never | `src/vtx/coding_agent/tools/grep.py:35` |
| `bash` | `True` | unless the command is safe | `src/vtx/coding_agent/tools/bash.py` |
| `edit` | `True` | always | `src/vtx/coding_agent/tools/edit.py` |
| `write` | `True` | always | `src/vtx/coding_agent/tools/write.py` |
| `skill` | `True` | always | `src/vtx/coding_agent/tools/skill.py` |
| `web`, `web_search` | `False` | never | `src/vtx/agent/tools/web.py:93` |
| `ask_user` | `False` | never | `src/vtx/agent/tools/ask_user.py:191` |
| `delegate_subagent` | `False` | never | `src/vtx/agent/tools/task.py:504` |
| `tool_search` | `False` | never | `src/vtx/coding_agent/tools/tool_search.py:74` |
| `codemode` | `True` | always | `src/vtx/agent/tools/codemode.py:188` |

Two of those need a note.

`delegate_subagent` is non-mutating from the parent's side because the sub-agent runs in
its own session and returns text; the sub-agent's own mutating calls are gated in that
session, not in yours (`src/vtx/agent/tools/task.py:504`).

`codemode` is marked mutating on purpose (`src/vtx/agent/tools/codemode.py:188-189`):

```python
mutating = True
"""Scripts call other tools, which may mutate. Marked mutating so the ..."""
```

A script is not a way around the gate. The sandbox wraps each call it makes
(`governed_invoker`, `src/vtx/codemode/governance.py:193`) and the wrapper turns a
`PROMPT` into a refusal, because a script has nobody to ask
(`src/vtx/codemode/governance.py:144-173`):

```text
<tool> was not run: it needs approval, and a script has nobody to ask. Call that tool
directly so the user can approve it, or use a read-only tool that needs no approval.
```

A gate that throws is also a refusal: "a gate that throws must not silently become
allow" (`src/vtx/codemode/governance.py:161-167`). The gate the sandbox uses is
`ConversationRuntime._codemode_permission` (`src/vtx/agent/runtime.py:889`), which
forwards to `check_permission` with the running config.

## How `bash` is classified

Everything else mutating prompts on every call. `bash` is the exception, because a
model that may not run `git status` alone is useless. The classifier
(`src/vtx/core/permissions.py:197-221`) allows a single read-only command and nothing
else:

```python
def _is_safe_bash_command(command: str) -> bool:
    if "\n" in command or "`" in command or "$(" in command or "<(" in command or ">(" in command:
        return False

    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";|&()><")
        tokens = list(lexer)
    except ValueError:
        return False

    if not tokens:
        return False

    for token in tokens:
        if token and all(c in _PUNCTUATION_CHARS for c in token):
            return False

    base = tokens[0]
    if "/" in base:
        base = base.rsplit("/", 1)[-1]

    if base == "git":
        return _is_safe_git_command(tokens)

    return base in SAFE_COMMANDS
```

Step by step:

1. **Reject outright** on a newline, a backtick, `$(`, `<(`, or `>(`. No multi-line
   commands and no substitution, safe or otherwise.
2. **Tokenize** with `shlex` in POSIX mode with `punctuation_chars=";|&()><"`. An
   unbalanced quote raises `ValueError`, which is a rejection, not an error.
3. **Reject any token made only of punctuation.** `_PUNCTUATION_CHARS` is
   `frozenset(";|&()><")` (`src/vtx/core/permissions.py:172`). This is why a pipe is
   fatal: `cat f | grep x` tokenizes with a bare `|` token, so the whole command
   prompts, even though both halves are safe.
4. **Take the first token and strip its directory**: `/usr/bin/cat` is treated as `cat`.
5. **`git` goes to the git-specific check.** Anything else must be in `SAFE_COMMANDS`.

### `SAFE_COMMANDS`

Seventeen read-only commands (`src/vtx/core/permissions.py:133-155`):

| | | | |
|---|---|---|---|
| `cat` | `head` | `tail` | `ls` |
| `pwd` | `wc` | `diff` | `which` |
| `file` | `stat` | `du` | `df` |
| `whoami` | `id` | `uname` | `date` |
| `realpath` | `dirname` | `basename` | |

### `SAFE_GIT_SUBCOMMANDS`

Ten read-only subcommands (`src/vtx/core/permissions.py:157-170`):

| | | | |
|---|---|---|---|
| `status` | `diff` | `log` | `show` |
| `rev-parse` | `describe` | `ls-files` | `ls-tree` |
| `blame` | `shortlog` | | |

The git check (`src/vtx/core/permissions.py:224-237`):

```python
def _is_safe_git_command(tokens: list[str]) -> bool:
    i = 1
    while i < len(tokens):
        if tokens[i] in ("-c", "--config-env") or tokens[i].startswith("--config-env="):
            return False
        if not tokens[i].startswith("-"):
            if tokens[i] not in SAFE_GIT_SUBCOMMANDS:
                return False
            # --output writes diff to a file, making it mutating
            return not any(t == "--output" or t.startswith("--output=") for t in tokens[i + 1 :])
        if tokens[i] in ("-C", "--git-dir", "--work-tree", "--namespace") and i + 1 < len(tokens):
            i += 2
            continue
        i += 1
    return False
```

- `-c` and `--config-env` are refused anywhere. They can set `core.pager` or
  `alias.*`, and either one can turn a read-only subcommand into code execution.
- The first token that is not a flag is the subcommand, and it must be allowlisted.
- Anything after the subcommand is not checked, so `git diff --stat -- src/` is safe.
- `--output` / `--output=` anywhere after the subcommand is refused: it writes a diff
  to a file.
- The search starts at index 1, so the leading `git` is skipped. A bare `git` with no
  subcommand falls off the end and returns `False`.

Worked cases:

| Command | Decision | Why |
|---|---|---|
| `git status --short` | ALLOW | allowlisted subcommand |
| `git diff -- src/` | ALLOW | allowlisted, no `--output` |
| `git log --oneline -20` | ALLOW | `--oneline` and `-20` are flags, ignored |
| `git -C /repo status` | ALLOW | `-C` consumes its value, then `status` matches |
| `git status \| cat` | PROMPT | the `\|` token is all punctuation |
| `git status && cat x` | PROMPT | the `&&` token is all punctuation |
| `git -c core.pager=cat log` | PROMPT | `-c` refused anywhere |
| `git diff --output=/tmp/d.txt` | PROMPT | `--output` writes a file |
| `git commit -m "x"` | PROMPT | `commit` is not in the allowlist |
| `git push` | PROMPT | `push` is not in the allowlist |
| `cat README.md` | ALLOW | allowlisted command |
| `/usr/bin/cat README.md` | ALLOW | directory stripped |
| `cat README.md \| head -5` | PROMPT | pipe token |
| `rm -rf build/` | PROMPT | not in `SAFE_COMMANDS` |
| `echo $(cat secret)` | PROMPT | `$(` rejected before tokenizing |
| `$(whoami)` | PROMPT | `$(` rejected before tokenizing |
| `` echo `whoami` `` | PROMPT | backtick rejected |
| `cat "unclosed` | PROMPT | `shlex` raises `ValueError` |

Practical consequence: chain reads into one call and they prompt. `cat a.txt b.txt` is
safe, `cat a.txt && cat b.txt` is not.

## What the prompt looks like

The gate runs in the turn loop, not in the tool (`src/vtx/agent/turn.py:1287-1292`):

```python
@staticmethod
def _needs_approval(pending: PendingToolCall) -> bool:
    # Unknown tools get ALLOW; they'll error in _execute_tool anyway
    if not pending.tool:
        return False
    decision = check_permission(pending.tool, pending.tool_call.arguments)
    return decision == PermissionDecision.PROMPT
```

When it prompts (`src/vtx/agent/turn.py:1029-1072`):

1. `permission_request` is emitted on the extension bus, with `permission`,
   `tool_name`, `arguments`, and `tool_call_id`.
2. The TUI shows the approval block with the tool name and a preview of the arguments.
3. On **approve**, the tool runs and `tool_execution_start` is emitted.
4. On **deny**, `permission_denied` is emitted and the model gets a tool result saying
   `Tool call denied by user. Ask them what they'd like you to do instead.` The tool is
   never executed. The turn continues; the model adapts rather than dying.

Cancelling the turn (Escape) does not answer the prompt; it stops the turn.

`ask_user` is not a permission tool. It is how the model asks you a question, and it is
non-mutating, so it never passes through this gate
(`src/vtx/agent/tools/ask_user.py:191`).

## Changing the mode

| How | Effect | Source |
|---|---|---|
| `/permissions` | Picker over `prompt` and `auto` | `src/vtx/tui/commands/settings.py:75-96` |
| `/permissions auto` | Set the mode without the picker | same |
| `alt+ctrl+p` | Toggle between the two modes | `src/vtx/tui/app.py:99`, `:865-868` |
| Config file | Edit `permissions.mode`, then `/reload` | `src/vtx/core/config.py:989` |
| `--dump-config` | Print the composed plugin rows for the profile | `src/vtx/coding_agent/cli.py:227` |

Headless cannot show a prompt, so `-p/--prompt` forces `auto` for the run and writes it
into the config file, restoring the previous value afterwards
(`src/vtx/coding_agent/headless.py:149-157`):

```python
previous_config_data = _read_config_data(_ensure_config_file())
previous_config_data.setdefault("permissions", {})["mode"] = "auto"
_set_config_version(previous_config_data)
_atomic_write_text(_ensure_config_file(), _serialize_config_yaml(previous_config_data))
reload_config()
```

That means a headless run mutates `~/.vtx/config.yml` while it is running. If the
process is killed mid-run, the setting stays on `auto`. It also means a mutating tool in
a headless run is never confirmed by a human.

## Rules from a plugin

`PermissionService` is the row a plugin uses to add rules
(`src/vtx/plugins/services.py:214-245`):

```python
class PermissionService:
    """Plugin-registered permission rules.

    The base decision comes from `vtx.core.permissions.check_permission`. Plugins
    contribute rules that can only ever *tighten* the decision: a rule returning
    `"prompt"` or `"deny"` escalates, and nothing downgrades to `allow` below the
    base decision.
    """

    def add_rule(
        self, rule: Callable[[str, dict[str, Any]], str | None]
    ) -> Callable[..., Any]:
        """Contribute a ``(tool_name, arguments) -> "allow" | "prompt" | "deny" | None`` rule."""
        self._rules.append(rule)
        return rule

    def decide(self, tool: Any, arguments: dict[str, Any], config: Any = None) -> str:
        base = check_permission(tool, arguments, config)
        outcome = "allow" if base is PermissionDecision.ALLOW else "prompt"
        for rule in self._rules:
            verdict = rule(getattr(tool, "name", ""), arguments)
            if verdict is None:
                continue
            if verdict == "allow":
                continue
            if verdict == "deny":
                return "deny"
            outcome = "prompt"
        return outcome
```

Read the loop carefully. `"allow"` from a rule is a no-op: it `continue`s, so it can
never override a base `PROMPT`. `"deny"` short-circuits to `"deny"`. `"prompt"` sets the
outcome to `"prompt"` but does not return, so a later rule can still `"deny"`. The
escalation is one-directional by construction, which is the point: no plugin can
silently auto-approve a destructive call.

A returning `None` means "no opinion" and is the normal shape for a rule that only
cares about one tool.

The rule layer ships as a builtin (`src/vtx/plugins/builtins/services/permissions.py`,
the whole file):

```python
from vtx.plugins.services import PermissionService


@plugin(name="permissions")
def apply(ctx, config):
    """Publish the permission gate.

    The base decision stays whatever the user configured; plugins add rules
    that can only tighten it, so no plugin can silently auto-approve a
    destructive call.
    """
    ctx.provide("permissions", PermissionService())


PLUGIN = apply
```

Add a rule from your own plugin:

```python
@plugin(name="no-force")
def apply(ctx, config):
    service = ctx.require("permissions")

    @service.add_rule
    def no_force_push(tool_name, args):
        if tool_name == "bash" and "--force" in str(args.get("command", "")):
            return "deny"
        return None
```

The contract is pinned by a test (`tests/plugins/test_builtins.py:195-204`):

```python
service.add_rule(lambda name, args: "allow" if name == "bash" else None)
service.add_rule(lambda name, args: "prompt" if "rm" in str(args) else None)
...
assert service.decide(Tool(), {"command": "rm -rf /"}) in {"prompt", "deny"}
```

## Gates declared by an agent file

An agent can carry its own permission posture (`src/vtx/agent/agents/schema.py:75-76`):

| Field | Type | Default | Meaning |
|---|---|---|---|
| `permission_mode` | `"auto" \| "prompt" \| None` | `None` | Overrides `permissions.mode` while the agent is active |
| `permission_gates` | `list[PermissionGate]` | `[]` | Per-tool rules with a `when` expression |

`PermissionGate` (`src/vtx/agent/agents/schema.py:27-44`):

| Field | Type | Meaning |
|---|---|---|
| `tool` | `str` | Tool name the gate applies to |
| `when` | `str` | Expression evaluated against the tool's arguments |
| `action` | `"allow" \| "deny" \| "prompt"` | Verdict |
| `reason` | `str \| None` | Shown to the model when the gate fires |

`activate.py` reads the active agent's mode and gates
(`src/vtx/agent/agents/activate.py:133-147`):

```python
def active_permission_mode(registry, default):
    agent = registry.active()
    if agent is not None and agent.definition.permission_mode is not None:
        return agent.definition.permission_mode
    return default


def active_permission_gates(registry):
    agent = registry.active()
    if agent is None:
        return []
    return list(agent.definition.permission_gates) + list(agent.local_gates)
```

`local_gates` are the ones registered from inside the agent file through
`api.permission_gate(...)` (`src/vtx/coding_agent/agents/api.py:341-365`), so
`permission_gates` in the file and `api.permission_gate()` in `register()` land in the
same list. See `agents.md` for the full `when` grammar.

## Known gaps

- **There is no plan mode.** Two values only, everywhere: the config
  (`src/vtx/core/config.py:44`), the built-in loop
  (`src/vtx/core/permissions.py:175-194`), and the agent schema
  (`src/vtx/agent/agents/schema.py:75`). There is nothing to document beyond that.
- **The turn loop does not consult `PermissionService.decide`.** `_needs_approval` calls
  `check_permission` directly (`src/vtx/agent/turn.py:1291`), so plugin rules and agent
  `permission_gates` are enforced only where a caller explicitly invokes `decide`:
  the SDK's policy classes (`src/vtx/agent/sdk/permissions.py`) and the codemode
  governance layer. A `"deny"` rule from a plugin does not block a tool call in an
  interactive TUI run today.
- **The base decision cannot deny.** Returning `deny` requires a plugin rule, and see
  the gap above. Without one, the only way a mutating tool is refused is you declining
  the prompt.
- **`bash` classification is lexical, not semantic.** `git diff > file` prompts (the
  `>` token), but `git stash` and `git checkout .` also prompt while
  `git show HEAD:file` does not, and `shellcheck`-style pipe rejection means two safe
  commands chained together prompt. Workarounds are usually one command per call.
- **`auto` applies to the sub-agents a session spawns too.** There is no per-sub-agent
  mode; a background sub-agent runs unattended
  (`src/vtx/agent/tools/task.py:616-621`).
- **Agents' `permission_mode: "auto"` is read for the active agent only.** Switching
  agents mid-session changes which gates exist, but nothing re-asks about a tool the
  new agent has forbidden.

Related pages: [tools.md](tools.md) for the tool schemas, [configuration.md](configuration.md)
for the config file, [agents.md](agents.md) for per-agent gates,
[plugins.md](plugins.md) for registering a permission row, [cli.md](cli.md) for the
flags that select a profile, and [codemode.md](codemode.md) for the sandbox's gate.

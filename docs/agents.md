# Agents

An agent file is a Python module that declares a switchable profile: its instructions, its
model, which tools it may touch, and the tools and slash commands it ships with. This page
covers where those files are discovered, the full `AgentDef` field reference, how allow and
deny lists compose the active tool set, how to switch agents at runtime, and how sub-agents
and handoff relate to the profile system.

## What lives in `.vtx/agent/`

An agent is one `.py` file, or a package directory with an `__init__.py`. Launch Vtx in a
directory with none of them and you get no agents at all: there are no built-in profiles.

```text
No built-in profiles. They used to be hardcoded here, which meant every session
carried a `plan` profile the user never asked for, and editing the shipped list was
the only way to change what the agent could be. Agents now come solely from
`.vtx/agent/<name>.py`; with none present the registry is empty and the runtime
falls back to its session-default behaviour, which is the same path it already took
when no agent was active.
```

(`src/vtx/agent/agents/loader.py:173-178`)

## Where agent files are discovered

`find_agent_paths` (`src/vtx/agent/agents/discovery.py:40-110`) searches three places in this
order:

| Order | Location | Notes |
|---|---|---|
| 1 | `<cwd>/.vtx/agent/*.py` and packages | Walked **up** to the git root, inclusive. A deeper directory wins over its ancestors. |
| 2 | `~/.vtx/agent/*.py` and packages | Global scope, shared by every project on the machine. |
| 3 | Explicit paths | `--agent-file <path>` (repeatable) and the `agents.files` config list (`src/vtx/core/config.py:219`) |

Paths are collected by `_candidate_paths` (`src/vtx/agent/agents/discovery.py:27-37`): a
`.py` file that is not `__init__.py`, or a directory containing `__init__.py`.

```bash
vtx --agent-file ./shared/agents/reviewer.py --agent-file ./shared/agents/docs.py
```

### Precedence

Both discovery and the loader drop a name they have already seen, so the **first** location
to claim a name keeps it: the deepest project directory, then shallower ones, then global
(`src/vtx/agent/agents/discovery.py:60-74`). Because configured paths are processed last,
an `--agent-file` whose name also exists in `.vtx/agent/` is silently ignored - see
Known gaps.

### The name is not yours to choose

The module must declare `AGENT.name`, and it must equal the file stem, or the package
directory name (`src/vtx/agent/agents/loader.py:122-126`):

```text
Agent /repo/.vtx/agent/reviewer.py: AGENT.name='Reviewer' does not match
file/package name 'reviewer'
```

The validator is strict (`src/vtx/agent/agents/schema.py:19-20`):

```python
AGENT_NAME_RE = re.compile(r"^(?!-)(?!.*--)[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
MAX_AGENT_NAME_LEN = 64
```

So: lowercase, digits, and single hyphens, no leading or trailing hyphen, no double hyphen,
at most 64 characters. `reviewer`, `code-reviewer`, and `docs-v2` are valid; `Reviewer`,
`reviewer-`, and `-reviewer` are not.

## The file format

Two top-level names, one required (`src/vtx/agent/agents/loader.py:106-144`):

| Name | Required | Purpose |
|---|---|---|
| `AGENT` | yes | An `AgentDef` instance (a dict is coerced through `AgentDef.model_validate`) |
| `register` | no | A **synchronous** function taking the `AgentAPI` |

The module is imported under a generated name, so a colliding module name cannot shadow a
real import (`src/vtx/agent/agents/loader.py:94-102`). A failure to import one agent does
not stop the others: `load_all_agents` returns `(loaded, errors)` and the errors are
collected (`src/vtx/agent/agents/loader.py:160-190`).

`register(api)` must be sync. Returning an awaitable raises:

```text
Agent /repo/.vtx/agent/docs.py: async register() is not supported; use a sync function
```

## `AgentDef` field reference

Declared in `src/vtx/agent/agents/schema.py`. `PermissionAction` is
`Literal["allow", "deny", "prompt"]` (`:23`), `InstructionsMode` is
`Literal["append", "replace"]` (`:22`), and `ThinkingLevel` is
`Literal["none", "minimal", "low", "medium", "high", "xhigh"]` (`:24`).

### Identity

| Field | Type | Default | Notes |
|---|---|---|---|
| `name` | `str` | required | Must match the file stem and `AGENT_NAME_RE` (`:54`) |
| `description` | `str` | required | 1-512 characters (`:55`); shown by `--list-agents` and `/agent` |
| `icon` | `str \| None` | `None` | At most 4 characters (`:56`) |
| `color` | `str \| None` | `None` | TUI accent (`:57`) |

### Model and run limits

| Field | Type | Default | Notes |
|---|---|---|---|
| `model` | `str \| None` | `None` | Overrides the session model while active (`src/vtx/agent/runtime.py:417`) |
| `provider` | `str \| None` | `None` | Overrides the provider (`src/vtx/agent/runtime.py:419`) |
| `base_url` | `str \| None` | `None` | **Declared, never read.** See Known gaps (`:62`) |
| `thinking_level` | `ThinkingLevel \| None` | `None` | Overrides the thinking level (`src/vtx/agent/runtime.py:421`) |
| `max_turns` | `int \| None` | `None` | Must be greater than 0 (`:64`) |

### Instructions

| Field | Type | Default | Notes |
|---|---|---|---|
| `instructions` | `str \| None` | `None` | Extra system-prompt text (`:67`) |
| `instructions_mode` | `"append" \| "replace"` | `"append"` | `append` adds to the base prompt; `replace` discards it (`:68`) |

The prompt is rebuilt on every activation (`_rebuild_system_prompt`,
`src/vtx/agent/runtime.py:435-471`), so switching takes effect on the next turn rather
than mid-response. `replace` throws away the assembled base prompt, including the tool
list, so an agent that uses it must restate anything the model needs.

### Tools

| Field | Type | Default | Notes |
|---|---|---|---|
| `tools_allow` | `list[str] \| None` | `None` | Empty strings are stripped (`:71`) |
| `tools_deny` | `list[str]` | `[]` | Empty strings are stripped (`:72`) |
| `tools` | `list[Any] \| None` | `None` | Raw SDK tools: `BaseTool`, callable, or `Agent` (`:93`) |
| `tool_groups` | `dict[str, list[str]] \| None` | `None` | Named named tool lists (`:103`) |
| `active_tool_group` | `str \| None` | `None` | Key into `tool_groups` (`:104`) |

`tools` entries are coerced into `LoadedAgent.local_tools`, which participate in the same
allow/deny pipeline as tools registered through `register(api)`
(`src/vtx/agent/agents/loader.py:146-155`). Anything else is rejected at load time:

```text
tools[0]: unsupported type Plan (expected BaseTool, callable, or Agent)
```

`tool_groups` keys are validated with the same pattern as agent names, and each group must
contain at least one tool (`src/vtx/agent/agents/schema.py:122-141`).
`active_tool_group` must name an existing key (`:150-161`).

### Permissions

| Field | Type | Default | Notes |
|---|---|---|---|
| `permission_mode` | `"auto" \| "prompt" \| None` | `None` | Overrides `permissions.mode` while active (`:75`) |
| `permission_gates` | `list[PermissionGate]` | `[]` | Per-tool rules (`:76`) |

`PermissionGate` (`src/vtx/agent/agents/schema.py:27-44`):

| Field | Type | Notes |
|---|---|---|
| `tool` | `str` | Tool the rule applies to |
| `when` | `str` | Expression evaluated against the tool's arguments |
| `action` | `"allow" \| "deny" \| "prompt"` | Verdict |
| `reason` | `str \| None` | Shown to the model when the gate fires |

`when` grammar, compiled by `_when_predicate` (`src/vtx/coding_agent/agents/api.py:77-123`):

| Form | Meaning |
|---|---|
| `<path> matches '<literal>'` | Substring: `literal in str(value)` |
| `<path> == '<literal>'` | Exact: `str(value) == literal` |

`<path>` is a dotted path into the arguments dict, so `command`, and `args.command`, and
`options.0.label` all resolve. The literal must be single- or double-quoted; anything else
raises `ValueError` **at registration time**, which means a typo fails at startup rather
than silently never firing.

### Integrations

| Field | Type | Default | Notes |
|---|---|---|---|
| `extensions` | `list[str]` | `[]` | Paths of extensions loaded only for this agent (`:83`) |
| `skills` | `list[str] \| None` | `None` | Skill names or paths to add when active (`:97`) |
| `handoffs` | `list[str]` | `[]` | **Declared, never read.** See Known gaps (`:79`) |
| `handoff_back` | `bool` | `True` | **Declared, never read** (`:80`) |
| `output_type` | `str \| None` | `None` | **Declared, never read** (`:86`) |
| `metadata` | `dict[str, Any]` | `{}` | Free-form, carried on the record (`:87`) |

`skills` filters the skill catalog down to the listed names or paths
(`src/vtx/agent/runtime.py:446-448`).

## How allow, deny, and groups compose

The filtering itself is `_filter` (`src/vtx/agent/agents/activate.py:25-76`), which is
exported as `_agent_tool_filter` and called directly by the runtime
(`src/vtx/agent/runtime.py:401-408`). `compose_active_tools`
(`activate.py:79-105`) is the same thing with the allow list taken from
`definition.tools_allow` and no group handling. The order of operations inside is the
whole contract:

| Step | Rule | Source |
|---|---|---|
| 1 | Load the default tools. If `tools_allow` is non-empty, also load any allowlisted **built-in** that is not in the default set, which is how you turn on `grep`. | `activate.py:38-43` |
| 2 | Add session-extension tools, then the active agent's `local_tools`. Agent-local wins over session extensions. | `activate.py:92-95` |
| 3 | Apply `tools_deny` as a set difference, minus `always_keep`. | `activate.py:65-67` |
| 4 | If `tools_allow` is non-empty, intersect with `allow | always_keep`. | `activate.py:72-74` |

`always_keep` is the set of the agent's own `local_tools` (`activate.py:99-103`):

```text
The agent's own local tools are exempt from its allow/deny filters: they were
explicitly contributed by the agent, not pulled from the base pool. This lets a
profile ship local tools while still restricting the built-in set.
```

Three consequences worth internalising:

- **`tools_allow` is not a filter on the defaults, it is a whitelist over the union.** A
  session extension's tool is dropped unless it is named in `tools_allow`.
- **A tool you ship can always be used.** `tools_deny` cannot remove your own
  `local_tools`, and neither can a tight allow list.
- **Deny runs before allow.** A name that appears in both lists is removed: deny wins for
  anything that is not one of the agent's own local tools.
- **`active_tool_group` beats `tools_allow` entirely.** If the group is set and non-empty,
  `tools_allow` is not consulted at all (`src/vtx/agent/runtime.py:385-393`).

```python
# excerpt from _compose_tool_allow, dedented
allow = None
if active is not None:
    group = active.definition.active_tool_group
    if group and active.definition.tool_groups:
        group_tools = active.definition.tool_groups.get(group)
        if group_tools:
            allow = list(group_tools)
    if allow is None and active.definition.tools_allow:
        allow = list(active.definition.tools_allow)
```

Note that this mapping lives in the runtime, not in `compose_active_tools`. A caller that
goes through `compose_active_tools` directly - the SDK, or anything building a sub-agent's
tool list - gets no group restriction, because that function only reads `tools_allow`.

Cycle the groups with `alt+ctrl+g` (`src/vtx/tui/app.py:101`). The cycle is
`[None, *group_keys]` (`src/vtx/agent/agents/registry.py:53-61`), and `cycle_tool_group`
writes the selected key - `None` included - straight into `definition.active_tool_group`
(`:131-143`). Reaching `None` turns group mode off, which makes the runtime fall back to
`tools_allow` (`src/vtx/agent/runtime.py:392-393`), so a profile with both can move between
them without editing anything.

## Switching agents

Every one of these writes `last_selected.agent` so the next session starts with the same
agent (`src/vtx/agent/runtime.py:260-309`).

| Surface | Effect | Source |
|---|---|---|
| `vtx --agent reviewer` / `-a` | Activate at startup | `src/vtx/coding_agent/cli.py:107-113` |
| `VTX_AGENT=reviewer` | Activate without a flag | `src/vtx/tui/app.py:260` |
| `agents.default` in config | Activate when neither of the above is set | `src/vtx/core/config.py:214` |
| `vtx --list-agents` | Print `name`, `description`, path, then exit | `src/vtx/coding_agent/cli.py:204-216` |
| `shift+tab` | Cycle the registry, wrapping through "no agent" | `src/vtx/tui/app.py:100` |
| `/agent` | Open the picker, which starts with "(no agent)" | `src/vtx/tui/commands/agents.py:87-107` |
| `/agent list` / `ls` | Table from `registry.describe()` | `src/vtx/tui/commands/agents.py:142-158` |
| `/agent current` / `status` | Name, icon, and description of the active agent | same |
| `/agent <name>` | Activate one | same |
| `/agent off` / `none` / `default` | Deactivate; `default` returns to `agents.default` | `src/vtx/tui/commands/agents.py:65-75` |
| `/agent reload` | Re-run discovery, keeping the active selection | `src/vtx/tui/commands/agents.py:44-63` |
| `/switch <name>` | Alias for `/agent <name>` | `src/vtx/tui/commands/switch.py:15-17` |
| `vtx --agent-file <path>` | Load an extra agent file or package (repeatable) | `src/vtx/coding_agent/cli.py:114-122` |
| `vtx --no-agents` | Skip discovery in `.vtx/agent/` and `~/.vtx/agent/` | `src/vtx/coding_agent/cli.py:123-127` |

Precedence for the startup agent is `--agent` over `VTX_AGENT` over `last_selected.agent`
over `agents.default` (`src/vtx/tui/app.py:261`).

`--list-agents` output is tab-separated, one agent per line, with `No agents found.` when
the registry is empty:

```text
reviewer	Read-only reviewer: audits a diff and reports findings	/repo/.vtx/agent/reviewer.py
docs	Docs editor: rewrites prose in docs/	/repo/.vtx/agent/docs.py
```

Load errors are printed to stderr as `agent error: <message>` and do not stop the run.

Switching emits events, so a plugin can react: `agent_activated` on the first activation
and `agent_changed` with `previous` and `current` on every change
(`src/vtx/agent/runtime.py:311-333`). Both are fired on the next loop iteration, not
synchronously from the caller.

## Agent-scoped tools and commands

`register(api)` gives the agent an `AgentAPI` (`src/vtx/coding_agent/agents/api.py:126`).
Everything registered on it exists only while the agent is active.

| Method | Purpose | Source |
|---|---|---|
| `api.local_tool(name, description, parameters, *, execute, mutating=True, label=None, ui_block=None)` | A tool only this agent has | `api.py:167-260` |
| `api.local_command(name, description, handler)` | A slash command only this agent has | `api.py:264-337` |
| `api.permission_gate(tool, *, when, action, reason=None)` | A permission rule scoped to this agent | `api.py:341-365` |
| `api.on(event, handler)` | Subscribe to a lifecycle event | `api.py:369-392` |
| `api.on_agent_change(handler)` | Shortcut for `api.on('agent_changed', ...)` | `api.py:394-398` |
| `api.notify(message, level="info")` | Log a user-facing line at `info`, `warning`, or `error` | `api.py:402-415` |

`local_tool` and `local_command` accept either an explicit `execute=`/`handler=` or the
decorator form. The execute callback is called as `execute(args, ctx)` where `args` is the
validated parameters dict and `ctx` is `{"cancel_event": ..., "cwd": ...}`
(`src/vtx/plugins/api.py:654-659`). Returning a string, a dict, or a `ToolResult` all work;
returning `None` becomes `(no output)`.

`local_command` handlers receive the argument string and may return a `CommandOutcome`, a
string, or `None`. An exception prints a traceback to stderr and yields
`(command raised an error)` rather than taking the session down
(`src/vtx/coding_agent/agents/api.py:301-313`).

Commands are merged over the base set and the owner is recorded
(`src/vtx/agent/agents/activate.py:108-130`), so `/help` shows
`(agent: <name>) <command>` and switching agents removes the command.

## Agent-scoped extensions

`AgentDef.extensions` holds paths to extension files loaded only for this agent. They are
matched against the session's already-loaded extensions by resolved path, and anything that
does not match is silently skipped (`_collect_extensions_for`,
`src/vtx/agent/agents/registry.py:154-172`). Global directories are always loaded too:
`.vtx/extensions/` and `~/.vtx/agent/extensions/`, unless `--no-extensions`
(`src/vtx/core/config.py:254`).

## Sub-agents

`delegate_subagent` (`src/vtx/agent/tools/task.py:497`) runs a second agent in its own
session and returns text. Specifying `subagent_type` by name picks one of **your** agent
files; there are no built-in presets (`src/vtx/agent/tools/task.py:110-136`):

```text
Only user-defined agents (`.vtx/agent/<name>.py`) name a sub-agent. There are no
built-in presets: anything the registry does not know about runs the default
sub-agent, which is the parent minus the parent-only tools.
```

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `description` | `str` | required | 1-128 chars, "Concise 3-5 word imperative label" (`task.py:30`) |
| `prompt` | `str` | required | The only context the sub-agent gets (`task.py:31`) |
| `subagent_type` | `str` | `""` | Agent name; empty runs the default (`task.py:32`) |
| `model` | `str \| None` | `None` | Model override (`task.py:33`) |
| `background` | `bool` | `False` | Return a `task_id` immediately (`task.py:34`) |

The sub-agent loses the parent-only tools, `delegate_subagent` and `goal`
(`src/vtx/coding_agent/tools/__init__.py:84`), inherits the parent's thinking level or
`"high"` (`task.py:207-225`), and gets a directive appended that forbids preamble, because
only its final text reaches the parent (`SUBAGENT_FINAL_ANSWER_DIRECTIVE`,
`task.py:160-173`).

The sub-agent's `ToolApprovalEvent` is auto-approved and its `AskUserEvent` auto-answered
(`task.py:433-438`). If the parent runs in `prompt` mode, the sub-agent still approves its
own gated calls; there is no per-sub-agent mode. Sub-agent concurrency is FIFO with no
priorities, capped by `task.max_concurrent` (default 4, `0` means unlimited)
(`src/vtx/agent/subagents.py`).

Background tasks have a specific cancellation contract (`task.py:616-621`): the parent's
cancel event does **not** stop them. Only an explicit stop or `ConversationRuntime.close()`
does.

## Handoff

`/handoff <query>` writes a fresh thread for a new session, with the generated prompt in the
input box, ready to go (`src/vtx/tui/commands/sessions.py:90-150`):

```text
/handoff implement phase two
```

The model is given a template that asks for an exact opening prompt
(`HANDOFF_PROMPT_TEMPLATE`, `src/vtx/core/handoff.py:4-35`):

```text
Task:
Context to keep:
Relevant files:
Constraints:
Next steps:
```

Plain text, no markdown fences, so it pastes cleanly into a new session. The runtime then
appends two hidden link entries (`src/vtx/agent/runtime.py:1658-1693`): a
`handoff_backlink` on the new session pointing at the old one, and a `handoff_forward_link`
on the old one pointing at the new. The backlink is what makes `/resume` render the handoff
tree (`src/vtx/agent/session.py:1092-1097`), and both are clickable in the transcript
(`src/vtx/tui/session_ui.py:135-160`). See `sessions.md` for the tree itself.

Note that `AgentDef.handoffs` and `handoff_back` are not what drives this. Nothing in the
runtime reads either field.

## Complete example

```python
"""Read-only code reviewer: audits a diff and reports findings, never edits."""

from pathlib import Path

from vtx.agent.agents.schema import AgentDef, PermissionGate

AGENT = AgentDef(
    name="reviewer",
    description="Read-only reviewer: audits a diff and reports findings without editing anything",
    icon="◈",
    color="cyan",
    model="gpt-5-mini",
    thinking_level="medium",
    max_turns=60,
    instructions=(
        "You review changes; you never make them. Read the diff, then read every file it "
        "touches, then report findings ordered by severity. Each finding needs a "
        "path:line. If you cannot point at a line, do not write the finding. Do not fix "
        "anything, and do not run anything that writes."
    ),
    instructions_mode="append",
    tool_groups={
        "read": ["read", "find", "grep"],
        "audit": ["read", "find", "grep", "bash"],
    },
    active_tool_group="read",
    tools_deny=["web", "web_search"],
    permission_mode="prompt",
    permission_gates=[
        PermissionGate(
            tool="bash",
            when="command matches 'git commit'",
            action="deny",
            reason="The reviewer never commits; committing is the author's call.",
        ),
        PermissionGate(
            tool="bash",
            when="command matches 'rm '",
            action="deny",
            reason="The reviewer never deletes anything.",
        ),
    ],
    skills=["vtx-tmux-test"],
    extensions=["review_report.py"],
)


def register(api):
    @api.local_tool(
        name="severity_check",
        description="Score a finding by how much of the runtime depends on the line. "
        "Returns high, medium, or low.",
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path of the file the finding is in",
                },
                "line": {
                    "type": "integer",
                    "description": "1-based line number of the finding",
                },
            },
            "required": ["path", "line"],
        },
        mutating=False,
    )
    def severity_check(args, ctx):
        path = args.get("path", "")
        if not path:
            return "low: no file path given"
        # Reviewer only scores lines that actually exist.
        text = Path(path).read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        line = int(args.get("line") or 0)
        if line < 1 or line > len(lines):
            return f"low: {path}:{line} is not a line in this file ({len(lines)} lines)"
        return f"medium: {path}:{line} exists; check how many callers reach it"

    @api.local_command(name="findings", description="Print the findings so far.")
    def findings(args):
        return "Findings are in the transcript above. Summarise them, ordered by severity."

    api.permission_gate(
        "bash",
        when="command matches 'git push'",
        action="deny",
        reason="No pushing from the reviewer.",
    )

    @api.on("agent_changed")
    def on_agent_changed(**payload):
        api.notify(f"agent changed: {payload}", level="info")
```

Save it as `.vtx/agent/reviewer.py`, confirm it loads, then start a session with it:

```bash
vtx --list-agents
```

```text
reviewer	Read-only reviewer: audits a diff and reports findings without editing anything	/repo/.vtx/agent/reviewer.py
```

```bash
vtx --agent reviewer
```

```text
/agent current
```

```text
Active agent: reviewer  --  Read-only reviewer: audits a diff and reports findings without editing anything
```

```text
/agent list
```

```text
[Handoff agents]
● reviewer
    Read-only reviewer: audits a diff and reports findings without editing anything
    tools: severity_check • commands: findings
    /repo/.vtx/agent/reviewer.py
```

What the example exercises, and why it is valid:

- `name` matches the stem `reviewer`, is lowercase, single-hyphen-free, under 64 chars.
- `tool_groups` with `active_tool_group="read"` means the group **replaces** `tools_allow`,
  so the base set is exactly `read`, `find`, and `grep`, plus `severity_check`. This profile
  sets no `tools_allow`, so there is nothing to fall back to. Switch with `alt+ctrl+g` to
  the `audit` group and `bash` appears.
- `tools_deny` names `web` and `web_search`, which the group already excludes, so it is
  belt and braces and costs nothing. It could not remove `severity_check` if it tried: the
  agent's own tools are always kept (`activate.py:99-103`).
- The `permission_gates` use the `matches` form on the `bash` `command` argument, which is
  substring containment.
- `skills` narrows the loaded catalog to the one skill named, so the model does not see the
  rest (`src/vtx/agent/runtime.py:446-448`).
- `extensions=["review_report.py"]` is resolved against the session's loaded extensions by
  absolute path, so a relative name only works when the path matches one that was loaded.
- `severity_check` is `mutating=False`, so it never prompts, and returns a string, which
  `ExtensionTool` converts into a `ToolResult` (`src/vtx/plugins/api.py:654-687`).
- `register` is sync, `permission_gate` uses a callable-free `when` string, and
  `@api.on("agent_changed")` subscribes to a real event name (`AGENT_CHANGED`,
  `src/vtx/plugins/event_contract.py`).

## Known gaps

- **The live package is `vtx.agent.agents`, not `vtx.coding_agent.agents`.** The runtime,
  TUI, headless entry point, and the `delegate_subagent` tool all import the former
  (`src/vtx/agent/runtime.py:12-14`, `src/vtx/tui/app.py:27`,
  `src/vtx/coding_agent/headless.py:177`). The `coding_agent` copy exists only to serve
  `--list-agents` (`src/vtx/coding_agent/cli.py:206`) and re-exports pieces of the live one.
  Its `schema.py` also declares `tools`, `skills`, `tool_groups`, and `active_tool_group`
  twice each (`src/vtx/coding_agent/agents/schema.py:93-121`), a merge artifact.
- **Four fields are declared but never read by the runtime:** `base_url` (:62), `handoffs`
  (:79), `handoff_back` (:80), and `output_type` (:86).
  `_apply_active_agent_to_runtime` reads only `model`, `provider`, and `thinking_level`
  (`src/vtx/agent/runtime.py:415-422`), and `create_handoff` never looks at `handoffs`.
  Setting them changes nothing.
- **Discovery precedence contradicts its own comments.** `_add` keeps the first stem it
  sees (`src/vtx/agent/agents/discovery.py:67-70`), and configured paths are added last, so
  on a name collision `--agent-file` loses to `.vtx/agent/`. The comment on `:68-70` claims
  "configured beats auto-discovered", which is the opposite of what the code does.
- **`agents.switch_mode` is not enforced anywhere.** It is declared and defaulted to `"lock"`
  (`src/vtx/core/config.py:215`) and documented as either "lock" (start a new session JSONL)
  or "hot" (re-render in place), but no code reads the value. Switching always behaves the
  same way, whichever value is set.
- **A single bad agent only costs you that agent.** The error string goes to stderr and
  startup continues (`src/vtx/agent/agents/loader.py:188-189`) - so a silent typo in
  `AGENT.name` looks exactly like the agent not existing.
- **Agent `permission_gates` are not consulted by the turn loop.** See
  [permissions.md](permissions.md#known-gaps).
- **`activate.py`'s docstring formula omits `always_keep`.** It says "minus deny, intersect
  with allow", but the agent's own local tools survive both filters
  (`src/vtx/agent/agents/activate.py:1-12`).

Related pages: [configuration.md](configuration.md) for the `agents:` config block,
[tools.md](tools.md) for the tool schemas a profile allows or denies,
[permissions.md](permissions.md) for the modes and gates, [sessions.md](sessions.md) for
handoff trees, [skills.md](skills.md) for the `skills` field, [plugins.md](plugins.md) for
the extension row, and [cli.md](cli.md) for the agent flags.

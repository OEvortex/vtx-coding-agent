# Tools

A tool is the only way the model acts on the world beyond talking. This page is the
complete reference: every tool Vtx registers, its exact parameter schema, and a worked
example. Read [how-vtx-works.md](how-vtx-works.md) for how a tool reaches the model in
the first place.

## The tool set

Vtx registers 14 tools. The composition decides which are on, so the authoritative
default set is the one the running profile resolves, not a constant in the source.

Run this to see what your profile actually boots with:

```bash
vtx --dump-config | grep -E "id:|disabled:"
```

| Tool | Default on | Mutating | Purpose |
|---|---|---|---|
| `read` | yes | no | Read a file or list a directory |
| `edit` | yes | **yes** | Replace an exact unique string in a file |
| `write` | yes | **yes** | Write a whole file |
| `bash` | yes | **yes** | Run a shell command |
| `find` | yes | no | Find files by glob |
| `grep` | no | no | Search file contents |
| `skill` | yes | **yes** | Load, create, and patch skills |
| `web` | yes | no | Web search via Exa (no API key) |
| `web_search` | no | no | Same search, different name |
| `ask_user` | yes | no | Ask the user a question mid-turn |
| `delegate_subagent` | yes | no | Run a sub-agent, foreground or background |
| `goal` | yes | no | Manage the session goal |
| `codemode` | yes | no | Run a Python script in a confined sandbox |
| `tool_search` | yes | no | Find a tool by what it does |

The default set is resolved by the plugin composition
(`ctx.tools.defaults()`, `src/vtx/plugins/services.py:118`). `grep` and `web_search`
are registered but off by default; turn one on by naming it in your profile's tool
list, or let `tool_search` surface it.

A tool marked **mutating** changes state and is therefore routed through the permission
gate before it runs. See [permissions.md](permissions.md).

## How a tool is described to the model

Each tool carries a JSON Schema derived from its `params` pydantic model
(`json_schema_to_pydantic`, `src/vtx/agent/tools/schema.py`), plus the tool's
description string. The model sees the name, the description, and the schema - never
the docstring. That is why the parameter descriptions below matter: they are the only
contract the model has.

---

## `read`

Read a file or list a directory. Non-mutating, so it never prompts.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `path` | `str` | required | Absolute path of file or directory |
| `offset` | `int \| None` | `None` | Start line (for large files) |
| `limit` | `int \| None` | `None` | Line count (for large files) |

Reads are capped at 2000 lines and 2000 characters per line
(`src/vtx/coding_agent/tools/read.py:18`), and a directory listing at 1000 entries. Use
`offset` and `limit` to page through anything larger.

```json
{"path": "/etc/nginx/nginx.conf"}
```

```json
{"path": "src/vtx/agent", "limit": 40}
```

Listing a directory returns its entries rather than file contents.

## `edit`

Replace an exact, unique string in an existing file. Mutating.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `path` | `str` | required | Absolute path of file to edit |
| `old_string` | `str` | required | Text to replace (exact match) |
| `new_string` | `str` | required | Replacement text (must differ) |
| `replace_all` | `bool` | `False` | Replace all occurrences |

`old_string` must match exactly once unless `replace_all` is set, which is the
guard against a silently-wrong edit (`src/vtx/coding_agent/tools/edit.py:19`). Read
the file first; the model has no file cache.

```json
{
  "path": "/repo/src/vtx/core/version.py",
  "old_string": "VERSION = \"1.2.2\"",
  "new_string": "VERSION = \"1.3.0\""
}
```

```json
{
  "path": "/repo/README.md",
  "old_string": "pi agent",
  "new_string": "vtx agent",
  "replace_all": true
}
```

## `write`

Write a whole file, creating it if needed. Mutating.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `path` | `str` | required | Absolute path of file to write |
| `content` | `str` | required | Full file contents to write |

`write` replaces the entire file. For a targeted change, use `edit` - a `write` over a
file you have not read destroys it.

```json
{
  "path": "/repo/notes.md",
  "content": "# Notes\n\nFirst line.\n"
}
```

## `bash`

Run a shell command. Mutating, and the tool most worth reading the permissions page
about.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `command` | `str` | required | Command line string to execute |
| `timeout` | `int` | `180` | Timeout in seconds |

The default timeout is 180 seconds (`src/vtx/coding_agent/tools/bash.py:20`). Output
is tail-truncated to the last 2000 lines and 50 KB, with the full log written to a temp
file (`src/vtx/coding_agent/tools/bash.py:21`, `:91`).

```json
{"command": "git status --short"}
```

```json
{"command": "uv run ruff check src/ && uv run ruff format --check src/", "timeout": 120}
```

There is no shell state between calls. `cd` in one call does not persist to the next;
chain with `&&` inside a single command.

## `find`

Find files and directories by glob. Non-mutating.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `pattern` | `str` | required | Glob pattern to match paths |
| `path` | `str \| None` | `None` | Directory to search within (defaults to cwd) |

Respects `.gitignore`. Results are sorted by modification time and truncated to 100
entries (`src/vtx/coding_agent/tools/find.py:21`).

```json
{"pattern": "*.py", "path": "src/vtx/agent"}
```

```json
{"pattern": "test_codemode*.py"}
```

## `grep`

Search file contents by regular expression or literal text. Registered but off by
default.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `pattern` | `str` | required | Regular expression or literal text to search for |
| `path` | `str \| None` | `None` | Directory to search within (defaults to cwd) |
| `glob` | `str \| None` | `None` | Restrict matches to paths matching this glob |

```json
{"pattern": "is_kernel_skill"}
```

```json
{"pattern": "def test_", "path": "tests", "glob": "test_skills*.py"}
```

For a semantic or ranked search rather than an exact one, use `tool_search` or the
repo's `vortexa` index described in [development.md](development.md).

## `web`

Search the web through Exa's MCP endpoint. No API key required.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `query` | `str` | required | Search query string or question |
| `num_results` | `int` | `8` | Number of results (1-20) |
| `search_type` | `str` | `"auto"` | `auto` (hybrid), `neural` (concept), `keyword` (exact) |
| `livecrawl` | `str` | `"fallback"` | `fallback` (cached/live), `always` (fresh), `never` (cached) |

```json
{"query": "Cloudflare Durable Objects SQLite storage limits", "num_results": 5}
```

`web_search` is the same tool under a second name, registered but off by default
(`src/vtx/agent/tools/web.py:134`). It exists so a provider that reserves `web` can
still reach the capability.

## `ask_user`

Ask the user a question and block the turn until they answer. Non-mutating, and the
only tool that stops the agent to get input.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `question` | `str \| None` | `None` | Single question (legacy shape; prefer `questions`) |
| `options` | `list[AskUserOptionParam] \| None` | `None` | 2-4 options; omit for free text |
| `multi_select` | `bool` | `False` | Allow multiple selections |
| `header` | `str \| None` | `None` | Short noun tag for the modal (max 16 chars) |
| `questions` | `list[AskUserQuestionParam] \| None` | `None` | 1-4 questions asked together in one tabbed dialog |

Use `questions` when you need several answers at once; use the single-question shape
only for a genuinely one-off. Options need 2-4 entries with a label and an
explanation (`src/vtx/agent/tools/ask_user.py:112`).

```json
{
  "header": "Deploy target",
  "question": "Which environment should this deploy to?",
  "options": [
    {"label": "staging", "description": "Deploy to the staging cluster and run smoke tests."},
    {"label": "production", "description": "Deploy to production immediately, no smoke tests."}
  ]
}
```

```json
{
  "questions": [
    {"header": "Scope", "question": "Which packages does this change touch?",
     "options": [{"label": "src only"}, {"label": "src + tests"}]},
    {"header": "Tests", "question": "Run the full suite?",
     "options": [{"label": "targeted only"}, {"label": "full suite"}]}
  ]
}
```

## `delegate_subagent`

Run a sub-agent in an isolated session. Non-mutating from the parent's perspective.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `description` | `str` | required | Concise 3-5 word imperative label, 1-128 chars |
| `prompt` | `str` | required | Detailed task instructions; the sub-agent cannot see this chat |
| `subagent_type` | `str` | `""` | Agent from `.vtx/agent/<name>.py`; empty runs the default with the parent's tool surface |
| `model` | `str \| None` | `None` | Model override (defaults to the parent agent's model) |
| `background` | `bool` | `False` | Return a `task_id` immediately; completion arrives next turn |

The prompt is the only context the sub-agent gets, so put everything it needs in
there (`src/vtx/agent/tools/task.py:30`).

```json
{
  "description": "Find the auth bug",
  "prompt": "In /repo, locate why /login returns 500 on an empty password. Read src/auth/, report the file and line of the root cause. Do not change anything.",
  "subagent_type": "researcher"
}
```

With `background: true` the call returns a task id instead of blocking. Do not poll;
the result arrives on a later turn.

```json
{"description": "Audit dependencies", "prompt": "List every dependency in pyproject.toml with a newer release.", "background": true}
```

## `goal`

Create and manage a persistent, multi-turn project goal with tasks, verification, and
audits.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `action` | `str` | required | `create`, `get`, `update`, `set_tasks`, `update_task`, `update`, or `list_orphans` |
| `objective` | `str \| None` | `None` | Complete goal objective and outcome to achieve |
| `mode` | `str \| None` | `None` | `regular` (open-ended) or `sisyphus` (strict sequential) |
| `verification` | `str \| None` | `None` | Completion contract or test command required for verification |
| `token_budget` | `int \| None` | `None` | Optional total-token ceiling |
| `status` | `str \| None` | `None` | `active`, `complete`, `blocked`, `paused`, or `revise` |

```json
{"action": "create", "objective": "Make `vtx plugin install` refuse an incompatible plugin with an actionable message", "verification": "uv run python -m pytest tests/plugins/", "mode": "regular"}
```

```json
{"action": "set_tasks", "tasks": [{"id": "t1", "text": "Add the version gate"}, {"id": "t2", "text": "Print the exemption command"}]}
```

## `codemode`

Run a Python script in a confined subprocess sandbox. Default-on.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `code` | `str` | required | Python written as a function body: top-level `return` works, and so does top-level `await` |

Tools are reached as `tools.<identifier>(**kwargs)` inside the script, so a gated tool
needs an admission the first time. `tools.search(query=..., limit=...)` lists what is
available (`src/vtx/agent/tools/codemode.py:104`).

```json
{"code": "results = []\nresults.append(tools.read(path='/repo/README.md'))\nreturn len(results)"}
```

```json
{"code": "async def main():\n    found = await tools.search(query=\"find dead code\", limit=5)\n    return found\nreturn await main()"}
```

The sandbox is a subprocess, not a persistent interpreter. There is no state between
two `codemode` calls. See [codemode.md](codemode.md).

## `tool_search`

Find a tool by describing what you need, rather than guessing its name.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `query` | `str` | required | What the tool should do, in words |
| `limit` | `int` | `8` | Maximum matches to return (1-50) |

```json
{"query": "replace text inside an existing file", "limit": 3}
```

This is the right first move when a capability might exist but the name is not
obvious - it is cheaper than a wrong call.

## `skill`

Load, create, and patch skills. Mutating, because `create`, `edit`, `patch`, and
`delete` write files.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `action` | enum | `"load"` | `load`, `list`, `view`, `create`, `patch`, `edit`, `delete`, `run` |
| `name` | `str \| None` | `None` | Skill name (lowercase/hyphens). Required except for `list` |
| `content` | `str \| None` | `None` | Full SKILL.md content; required for `create` and `edit` |
| `old_string` | `str \| None` | `None` | Unique exact text to replace; required for `patch` |
| `new_string` | `str \| None` | `None` | Replacement text; required for `patch` |
| `file_path` | `str \| None` | `None` | Supporting file to target (defaults to `SKILL.md`) |
| `scope` | `"project" \| "global"` | `"project"` | Target scope for `create` |

```json
{"action": "load", "name": "vtx-self-extend"}
```

```json
{"action": "list"}
```

```json
{"action": "create", "name": "my-skill", "content": "---\nname: my-skill\ndescription: Do X when the user asks for X\n---\n\n# My Skill\n\nSteps.\n", "scope": "project"}
```

```json
{"action": "patch", "name": "my-skill", "old_string": "Steps.", "new_string": "1. Do this."}
```

`action: "run"` returns the skill file rather than executing it, because there is no
persistent Python kernel any more (`src/vtx/coding_agent/tools/skill.py:229`). A
skill's body is instructions for a model, and the model reads them.

See [skills.md](skills.md) for the full authoring contract.

---

## Known gaps

- There is no filesystem watcher. A skill or extension written mid-session is picked
  up by `/reload` or a restart, never automatically.
- `bash` shares no state between calls, so multi-step shell work must be one command.
- `codemode` is a subprocess per call; there is no persistent interpreter to hold
  variables across calls.
- `grep` and `web_search` are off by default, so an agent that needs them must have
  them enabled by profile or find them with `tool_search`.
- A tool that a plugin registers by overriding a built-in's name replaces it
  (`ToolService.assemble`, `src/vtx/plugins/services.py:118`), which is powerful but
  means a buggy plugin can silently take `bash` away.

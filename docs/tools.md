# Tools

Vtx ships 11 built-in tools. Ten are enabled by default; `grep` is built in but opt-in (enable it via an extension, agent `tools_allow`, or a custom tool list).

| Tool | Does | Default |
| --- | --- | --- |
| `read` | Read files, list directories, view images | yes |
| `edit` | Exact search-and-replace in a file | yes |
| `write` | Create or overwrite a file | yes |
| `bash` | Run shell commands | yes |
| `find` | Glob file discovery (`fd`) | yes |
| `skill` | Manage skill workflows | yes |
| `web` | Web search (Exa neural) | yes |
| `ask_user` | Ask the user a clarifying question | yes |
| `delegate_subagent` | Dispatch an isolated sub-agent | yes |
| `goal` | Persistent project goals: create, track tasks, complete with audit | yes |
| `codemode` | Run a confined script that calls the other tools | yes |
| `grep` | Search file contents (`ripgrep`) | no |

All tools are `BaseTool` subclasses with Pydantic params. The `mutating` flag drives permission gating: non-mutating tools run without approval, mutating tools follow the permission mode (see [permissions.md](permissions.md)).

MCP server tools join the same set. They are `BaseTool` subclasses like any other, so they are gated, rendered, and interruptible identically; a server that annotates a tool `readOnlyHint` gets it registered as non-mutating and therefore no approval prompt. Their names are `mcp__<server>__<tool>`. A connected server also brings three session-level tools for its *resources* — `list_mcp_resources`, `list_mcp_resource_templates`, and `read_mcp_resource` — which take a `server` argument rather than existing per server, so three cover any number of servers. They appear only when something is connected, since a tool that can only return an empty list is noise. See [mcp.md](mcp.md) for configuration and behaviour.

## read

Read a file or directory.

| Param | Type | Notes |
| --- | --- | --- |
| `path` | string, required | Absolute path of file or directory |
| `offset` | int | Start line, for large files |
| `limit` | int | Line count |

Truncates at 2,000 lines / 2,000 chars per line. Directories render as annotated listings. Images are detected by extension and sent as vision content after downscaling (max 2,000 px / 4 MB).

## edit

Replace exact text in a file.

| Param | Type | Notes |
| --- | --- | --- |
| `path` | string, required | Absolute path |
| `old_string` | string, required | Must match exactly, including whitespace |
| `new_string` | string, required | Must differ from `old_string` |
| `replace_all` | bool | Replace every occurrence |

Fails unless `old_string` matches exactly once (unless `replace_all`). Returns a unified diff preview.

## write

Create or overwrite a file, making parent directories as needed.

| Param | Type | Notes |
| --- | --- | --- |
| `path` | string, required | Absolute path |
| `content` | string, required | Full file content |

## bash

Run a command in the working directory.

| Param | Type | Notes |
| --- | --- | --- |
| `command` | string, required | Shell command |
| `timeout` | int | Seconds; default 180 |

Output is truncated to the last 2,000 lines / 50 KB — when truncated, the full output is written to a temp file whose path is returned. ANSI escapes are stripped. Cancelling kills the whole process tree.

## find

Find files by glob via `fd`. Respects `.gitignore`; results are capped at 100 and sorted by modification time. Vtx auto-downloads `fd`/`rg` into `~/.vtx/bin` if missing.

| Param | Type | Notes |
| --- | --- | --- |
| `pattern` | string, required | Glob pattern, e.g. `*.py`, `**/*.json` |
| `path` | string | Directory to search (default: cwd) |

## grep

Search file contents by regex via `ripgrep`. Max 100 results / 30 KB output.

| Param | Type | Notes |
| --- | --- | --- |
| `pattern` | string, required | Text or regex |
| `path` | string | Dir or file to search (default: cwd) |
| `glob` | string | File filter glob, e.g. `*.py` |

## skill

Load, list, view, create, patch, edit, or delete skills. See [skills.md](skills.md) for the format.

`load` is the normal way to use a skill: the system prompt advertises skills by
name and description only, and `load` returns the SKILL.md body, the skill's
directory, and the files beside it, so relative paths inside a skill resolve
without a separate read.

| Param | Type | Notes |
| --- | --- | --- |
| `action` | enum | `load`, `list`, `view`, `create`, `patch`, `edit`, `delete` |
| `name` | string | Skill name; required except for `list` |
| `content` | string | Full SKILL.md content; required for `create`/`edit` |
| `old_string` / `new_string` | string | Find/replace pair for `patch` |
| `file_path` | string | Supporting file to target (default: SKILL.md) |
| `scope` | enum | `project` (`.agents/skills`) or `global` (`~/.agents/skills`) |

`list` omits python skills, which are hidden because nothing can execute them — see [skills.md](skills.md#python-skills). `run` returns the skill file rather than executing it: the persistent Python kernel it used to hand off to was removed with the RLM mode.

## web

Web search through Exa's MCP endpoint. Needs internet access.

| Param | Type | Notes |
| --- | --- | --- |
| `query` | string, required | Search query |
| `num_results` | int | 1–20, default 8 |
| `search_type` | string | `auto` (default), `neural`, or `keyword` |
| `livecrawl` | string | `fallback` (default), `always`, or `never` |

An alias named `web_search` is registered for the same tool.

## ask_user

Ask the user a clarifying question and block on the answer. Rendered as an interactive picker in the TUI.

| Param | Type | Notes |
| --- | --- | --- |
| `question` | string, required | Short, specific question (max 500 chars) |
| `options` | list | 2–4 options, each `{label, description}`; omit for free text |
| `multi_select` | bool | Allow multiple selections |
| `header` | string | Modal title tag (max 12 chars) |

## delegate_subagent

Dispatch a fresh sub-agent with its own tools, session and system prompt. It cannot see this conversation — put all context in `prompt`.

| Param | Type | Notes |
| --- | --- | --- |
| `description` | string, required | 3–5 word imperative label |
| `prompt` | string, required | Full instructions incl. context |
| `subagent_type` | string | Name of an agent in `.vtx/agent/<name>.py`; default: the default sub-agent |
| `model` | string | Model override (default: parent's) |
| `background` | bool | Run concurrently; returns a task ID now, result delivered when it lands |

There are no built-in sub-agent presets: `subagent_type` is matched against the agents loaded from `.vtx/agent/` and `~/.vtx/agent/`, and an unknown or empty name runs the default sub-agent (the parent's tool surface and instructions, 200-turn budget).

At most `task.max_concurrent` sub-agents run at once (default 4, `0` = uncapped). The rest wait in a FIFO queue — the pinned **Agents** panel above the editor lists the running ones and the queued count, and the info bar repeats `N running, M queued agents`. A config reload resizes the live queue.

With `background: true` the dispatch returns a `task_id` and the sub-agent keeps working after the turn ends. When it lands, the session resumes itself: the result is injected into the conversation and the agent gets a turn to act on it, so you do not have to send a message to collect an answer you already paid for. A wake-up turn can dispatch again, so cascading resumes stop after a few and the chat says so — the results are still there to read.

Results are capped at 32,000 chars with the last 200 transcript lines attached.

## goal

One action-dispatched tool for the persistent goal system (see [goals.md](goals.md)). All actions operate on the focused goal; only the top-level session has this tool.

| Param | Type | Notes |
| --- | --- | --- |
| `action` | enum, required | `create`, `get`, `update`, `set_tasks`, `update_task` |
| `objective` | string | `create`: complete outcome (1–4000 chars) |
| `mode` | string | `create`: `regular` (default) or `sisyphus` |
| `verification` | string | `create`: completion contract text |
| `token_budget` | int | `create`: total-token budget; goal becomes `budget_limited` at the cap |
| `status` | string | `update`: `complete`, `blocked`, `paused`, `active` (resume), `revise` |
| `reason` | string | `update`: required for `blocked`; change notes for `revise` |
| `completion_summary` | string | `update` + `complete`: short claim of satisfaction (auditor-checked) |
| `review_feedback` | string | `update`: auditor changes-required feedback to record |
| `tasks` | list | `set_tasks`: flat parent-linked items `{title, id?, parent_id?, note?}` |
| `task_id` | string | `update_task`: target task id, e.g. `t3.2` |
| `task_status` | string | `update_task`: `start`, `complete`, `skipped`, `pending` (reopen) |
| `evidence` | string | `update_task`: required when completing a task with a `Contract:` note |
| `note` | string | `update_task`: skip reason or note |
| `subtasks` | list | `update_task`: attach subtasks under the target |

`status="complete"` records the claim, then runs an independent auditor sub-agent over the workspace; the goal archives on `<approved/>` and stays open with feedback otherwise. The tool is non-mutating for permission purposes — archiving requires explicit user confirmation.

## codemode

Run a Python script that calls the agent's other tools. Write it as a function
body: `return` the value you want back, and `await` any tool call, including
several at once with `asyncio.gather`.

Where every other tool is one operation, this is an interpreter. The payoff is
that N tool calls cost one model turn instead of N, and that filtering, sorting,
and aggregation happen in code rather than in the model's context.

| Param | Type | Notes |
| --- | --- | --- |
| `code` | string | The script |

Inside the script: `tools.<name>(**kwargs)` calls a tool, `tools.search(query=...)`
finds tools when the catalog is partial, `text(value)` appends to the
model-visible output, and `store`/`load` carry JSON values between calls.

The sandbox has no filesystem, network, subprocess, `eval`/`exec`/`compile`, or
`open`, and can only import a short standard-library allowlist — every file and
network operation has to go through a tool.

Side effects are real: a script that fails partway does not undo the calls that
already ran. Marked mutating for the same reason `bash` is.

Because a script can reach `bash`, a profile that wants to keep the shell out of
scripts has to name `codemode` in `tools_deny`. There is no built-in `plan`
profile to edit — vtx ships no built-in agents — see [agents.md](agents.md).

Tools a script calls are governed like the model's own: the same extension
hooks, the same argument rewriting, and the same permission decision, where
*prompt* becomes a refusal because a script has nobody to ask.

MCP servers contribute their tools to a script's tool set, grouped under the
server's namespace; see [mcp.md](mcp.md) for the `exposure` setting that decides
whether a tool is listed, merely callable, or hidden.

See [codemode.md](codemode.md) for the sandbox contract, the failure taxonomy,
the enforced budgets, and how the isolation is enforced.

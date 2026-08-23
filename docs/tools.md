# Tools

Vtx ships 10 built-in tools. Nine are enabled by default; `grep` is built in but opt-in (enable it via an extension, agent `tools_allow`, or a custom tool list).

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
| `task` | Dispatch a sub-agent | yes |
| `grep` | Search file contents (`ripgrep`) | no |

All tools are `BaseTool` subclasses with Pydantic params. The `mutating` flag drives permission gating: non-mutating tools run without approval, mutating tools follow the permission mode (see [permissions.md](permissions.md)).

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

List, view, create, patch, edit, or delete skills. See [skills.md](skills.md) for the format.

| Param | Type | Notes |
| --- | --- | --- |
| `action` | enum | `list`, `view`, `create`, `patch`, `edit`, `delete` |
| `name` | string | Skill name; required except for `list` |
| `content` | string | Full SKILL.md content; required for `create`/`edit` |
| `old_string` / `new_string` | string | Find/replace pair for `patch` |
| `file_path` | string | Supporting file to target (default: SKILL.md) |
| `scope` | enum | `project` (`.agents/skills`) or `global` (`~/.agents/skills`) |

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

## task

Dispatch a fresh sub-agent with its own tools, session and system prompt. It cannot see this conversation — put all context in `prompt`.

| Param | Type | Notes |
| --- | --- | --- |
| `description` | string, required | 3–5 word imperative label |
| `prompt` | string, required | Full instructions incl. context |
| `subagent_type` | string | Preset name or user agent; default `general-purpose` |
| `model` | string | Model override (default: parent's) |
| `background` | bool | Run concurrently; returns a task ID now, result arrives next turn |

Built-in presets (overridable under `task.subagent_presets` in config):

- **general-purpose** — full default tool set, 200-turn budget.
- **Explore** — read-only investigation; tools limited to `read`, `find`, `skill`, `web`.
- **Plan** — read-only planner that produces a step-by-step plan without touching files.

Results are capped at 32,000 chars with the last 200 transcript lines attached.

# Tools

Vtx gives the model a small, predictable set of tools. They are implemented in `src/vtx/tools/` as Pydantic-validated `BaseTool` subclasses. The tool JSON schemas sent to the model are deliberately slim (no `title`/`minLength` noise, optional fields collapsed) to keep the prompt footprint small; pydantic still enforces every validation constraint on the actual call.

## Summary

| Tool | Action | Mutating |
|------|--------|----------|
| `read` | Read a file or directory (pagination + images) | No |
| `edit` | Exact text search-and-replace | **Yes** |
| `write` | Create/overwrite a file | **Yes** |
| `bash` | Run a shell command in the cwd | **Yes** |
| `find` | Glob file discovery (fd) | No |
| `grep` | Regex search over file contents (ripgrep) | No |
| `skill` | Manage skill workflows | Depends |
| `web` | Web search (Exa neural) | No |
| `ask_user` | Ask a clarifying question and wait | No |
| `task` | Dispatch a sub-agent | No |

Mutating tools (`bash`, `edit`, `write`) are gated by the permission mode (`prompt` or `auto`) — see [permissions.md](permissions.md).

---

## read

Read a file or directory. Truncates to 2000 lines / 2000 chars per line; use `offset`/`limit` to paginate large files. Supports `jpg`/`jpeg`/`png`/`gif`/`webp` images.

Parameters:
- `path` (string, required) — absolute path of file or directory.
- `offset` (int, optional) — start line for large files.
- `limit` (int, optional) — line count for large files.

## edit

Replace exact text in a file. `old_string` must match exactly, including whitespace.

Parameters:
- `path` (string, required) — absolute path of file to edit.
- `old_string` (string, required) — exact text to replace.
- `new_string` (string, required) — replacement text (must differ).
- `replace_all` (bool, default `false`) — replace all occurrences.

## write

Write a file. Creates or overwrites; makes parent directories. Use `edit` for partial changes.

Parameters:
- `path` (string, required) — absolute path of file to write.
- `content` (string, required) — file content.

## bash

Run a bash command in the cwd. Output is truncated to the last 2000 lines / 50KB (full output is written to a temp file). Do not use it for file search (`find`), reading (`read`), or editing (`edit`).

Parameters:
- `command` (string, required) — the command to execute.
- `timeout` (int, default 180) — timeout in seconds.

## find

Find files by glob using `fd`. Returns paths sorted by mtime, respects `.gitignore`, truncated to 100 results.

Parameters:
- `pattern` (string, required) — glob, e.g. `*.py`, `**/*.json`.
- `path` (string, optional) — directory to search (default: cwd).

## grep

Search file contents by regex using `ripgrep`. Returns matching lines with `path:line`, respects `.gitignore`, truncated to 100 matches.

Parameters:
- `pattern` (string, required) — text or regex to search for.
- `path` (string, optional) — dir or file to search (default: cwd).
- `glob` (string, optional) — file filter glob, e.g. `*.py`.

## skill

List, view, create, patch, edit, or delete skill workflows.

Actions: `list`, `view`, `create`, `patch`, `edit`, `delete`.

Parameters:
- `action` (enum, default `view`)
- `name` (string, optional) — skill name (lowercase/hyphens); required except for `list`.
- `content` (string, optional) — full `SKILL.md` content; required for `create`/`edit`.
- `old_string` (string, optional) — unique text to find (`patch`).
- `new_string` (string, optional) — replacement text (`patch`).
- `file_path` (string, optional) — supporting file to target (default: `SKILL.md`).
- `scope` (enum, default `project`) — `project` (`.agents/skills`) or `global` (`~/.agents/skills`) for `create`.

See [skills.md](skills.md).

## web

Web search tool backed by the Exa neural endpoint. Returns titles, URLs, and
snippets. Needs internet.

Parameters:
- `query` (string, required)
- `num_results` (int, default 8, 1–20)
- `search_type` (string, default `auto`) — `auto`, `neural`, or `keyword`.
- `livecrawl` (string, default `fallback`) — `fallback`, `always`, or `never`.

## ask_user

Ask the user a clarifying question and wait. Pass 2–4 options for multiple choice, or omit for free text. The user can always type a custom answer.

Parameters:
- `question` (string, required, 1–500 chars) — keep it short; put choices in `options`, not here.
- `options` (list, optional) — 2–4 options; each has `label` (required, short unique) and `description` (optional).
- `multi_select` (bool, default `false`) — allow multiple selections.
- `header` (string, optional, max 12 chars) — short noun tag for the modal.

## task

Dispatch a fresh sub-agent (own tools/session, cannot see this chat) for a self-contained task; returns only its final text. `background: true` returns a `task_id` and delivers the answer in the next turn. Not for trivial single-tool work.

Parameters:
- `description` (string, required, 1–128 chars) — 3–5 word imperative label, e.g. "Find the auth bug".
- `prompt` (string, required) — full instructions including all context.
- `subagent_type` (string, default `general-purpose`) — `general-purpose`, `Explore`, `Plan`, or a user agent name.
- `model` (string, optional) — model override (default: parent's).
- `background` (bool, default `false`) — run concurrently, return `task_id` now.

Built-in sub-agent presets are configured under `task.subagent_presets` in `config.yml` (see [configuration.md](configuration.md)).

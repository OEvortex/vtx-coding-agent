# Sessions

Every Vtx conversation is persisted as a JSONL file so you can resume, review, or export it.

## Storage

Sessions are stored per-project under your config directory:

```
~/.vtx/sessions/<safe_cwd>/<timestamp>_<session_id>.jsonl
```

`<safe_cwd>` is a filesystem-safe slug of the current working directory, so each project keeps its own session history. Each line in the `.jsonl` file is one event (a message, tool call, or system event) — see the schema below.

## Commands

- `vtx -c` / `--continue` — resume the most recent session for the cwd.
- `vtx -r <id>` / `--resume <id>` — resume a specific session by id or id prefix.
- `/resume` — interactive session-history browser in the TUI.
- `/session` — show active session statistics and token usage.
- `/new` — start a fresh conversation and reload project context.
- `/handoff <query>` — summarize the current session and start a new, clean session carrying that context forward (see [agents.md](agents.md)).
- `/export` — export the current transcript to a standalone HTML file.

## JSONL schema

Each line is a JSON object with a `type` field. Common types:

- `message` — an assistant or user message (`role`, `content`, `timestamp`).
- `tool_call` — a tool invocation (`name`, `args`, `timestamp`).
- `tool_result` — the output of a tool call (`name`, `success`, `result`).
- `goal` — a goal-mode entry (`objective`, `active`).
- `header` — session metadata (`version`, `cwd`, `model`).

Older sessions that never wrote a goal entry are still readable; the loader migrates them on load.

## Programmatic access

```python
from vtx.session import Session

# List sessions for a project
for info in Session.list("/path/to/project"):
    print(info.id, info.created_at)

# Load one
session = Session.load(session_file_path)
```

## Compaction

When a session approaches the model's context limit, Vtx auto-compacts older turns into a summary (controlled by `compaction.on_overflow` and `compaction.threshold_percent` in `config.yml` — see [configuration.md](configuration.md)). Use `/compact` to trigger it manually.

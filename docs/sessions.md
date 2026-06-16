# Sessions

Vtx persists every conversation as an append-only JSONL file. Sessions are easy to inspect, archive, move around, and resume later. This doc covers the file format, the resume flow, handoff, `/export`, and compaction.

## Where sessions live

```text
~/.vtx/sessions/<safe-cwd>/<session-id>.jsonl
```

- `<safe-cwd>` is the working directory at session creation, with `/` and `\` replaced by `-` and leading/trailing `-` stripped. Example: `/home/me/projects/foo` becomes `home-me-projects-foo`.
- The directory is created with mode `0700` (sessions can contain sensitive content).
- `<session-id>` is an 8-character hex string (e.g. `3f2a8c1b`).

The full list of on-disk paths is in [storage-layout.md](storage-layout.md).

## File format

Each file is JSONL (one JSON object per line). The first line is always the session header; subsequent lines are entries.

### Header

```json
{
  "type": "header",
  "version": 1,
  "id": "3f2a8c1b",
  "timestamp": "2026-06-15T12:34:56+00:00",
  "cwd": "/home/me/projects/foo",
  "system_prompt": "You are an expert coding assistant called Vtx. ...",
  "tools": ["read", "edit", "write", "bash", "find"],
  "initial_thinking_level": "high"
}
```

`system_prompt` is persisted so a resumed session reconstructs the same prompt the model was originally given. `tools` records which tools were active. `initial_thinking_level` is the thinking level the session started with.

### Message entry

```json
{
  "type": "message",
  "id": "<entry-id>",
  "parent_id": "<previous-entry-id>",
  "timestamp": "2026-06-15T12:35:01+00:00",
  "message": {
    "role": "user",
    "content": [{"type": "text", "text": "fix the failing test"}]
  }
}
```

The `message` field is the full Pydantic message object — user, assistant, or tool_result. Tool calls live on assistant messages; tool results live on dedicated `tool_result` messages that follow.

### Model change entry

```json
{
  "type": "model_change",
  "id": "...",
  "parent_id": "...",
  "timestamp": "...",
  "provider": "deepseek",
  "model_id": "deepseek-v4-flash",
  "base_url": null
}
```

Recorded when you run `/model` mid-session. On resume, the model picker starts at this entry.

### Thinking level change entry

```json
{
  "type": "thinking_level_change",
  "id": "...",
  "parent_id": "...",
  "timestamp": "...",
  "thinking_level": "high"
}
```

Recorded on `/thinking <level>`. Persists across resume.

### Compaction entry

```json
{
  "type": "compaction",
  "id": "...",
  "parent_id": "...",
  "timestamp": "...",
  "summary": "...full conversation summary...",
  "first_kept_entry_id": "...",
  "tokens_before": 87432,
  "details": null
}
```

Records a context-window overflow. The summary becomes a single user message that the next agent run starts with. The full pre-compaction history is still on disk in the file — compaction only changes what the model sees, not what's stored.

### Custom message entry

Used for slash-command feedback (`/handoff`, `/export`, etc.) and handoff markers. Has a `custom_type` string and a `content` string.

### Leaf entry

Marks a "this is the current conversation leaf" pointer. The session tree (when shown in `/resume`) is built from the parent/child graph implied by `parent_id`.

## Resume and continue

Three ways:

| Entry point | What it does |
| --- | --- |
| `vtx -c` / `vtx --continue` | Resume the most recent session. |
| `vtx -r <id>` / `vtx --resume <id>` | Resume a specific session. `<id>` can be the full 8-char hex or a unique prefix. |
| `/resume` (in-app) | Open the interactive session picker. Arrow keys to navigate, Enter to resume, `Ctrl+D` to delete. |

When a session is resumed:

1. Vtx loads the JSONL, replays messages in order, and rebuilds the message list.
2. The provider is reinitialized with the session's model, thinking level, and provider.
3. The system prompt is loaded from the header (or rebuilt if missing).
4. Any `model_change` / `thinking_level_change` / `compaction` entries before the leaf are applied so the resumed session is in the same state as when you left it.

The session is appended, not replaced — the original entries are preserved, new entries are added on top.

## Handoff

`/handoff <query>` starts a focused new session based on the current one:

1. The current message history is sent to the LLM with a handoff-summarization prompt (see [`src/vtx/core/handoff.py`](../src/vtx/core/handoff.py)).
2. The model produces a fresh opening user message that captures the relevant context for the new goal.
3. A new session file is created. The opening message is its first entry.
4. A "Handoff → <new-session-id>" marker is added to the original session and the new one, so `/resume` shows them as linked in a tree.

The model is told (in the handoff prompt) to:

- Focus on the new goal only — drop irrelevant context.
- Preserve decisions, constraints, and assumptions.
- Include file paths that matter and why.
- Note current status: done / in progress / next action.
- Not invent facts (use "Unknown" for gaps).
- Not include backlinks or UI metadata.
- Output plain text — no markdown fences.

The resulting prompt is what you'd manually type to onboard a fresh session, so it's safe to skim and adjust.

## Export

`/export` writes a standalone HTML transcript to the current working directory:

```text
vtx-export-<session-id>-<timestamp>.html
```

The HTML is self-contained — inline CSS, no external assets, no JS. It can be opened in any browser, attached to a bug, archived, or pasted into a doc. The transcript includes:

- Session metadata (id, date, cwd, model, thinking level, tools used).
- The full message history with rendered Markdown.
- Tool calls with their arguments and results.
- Thinking blocks (rendered as a collapsible region by default).
- Compaction summaries inline at the right place.

`/copy` is the lighter-weight sibling — it copies the last assistant response to the clipboard via the platform-native clipboard tool. No file is written.

## Compaction

When the running token total reaches `compaction.threshold_percent`% of the model's context window, Vtx triggers compaction.

### Trigger formula

```text
overflow = (input + output + cache_read + cache_write) >= (threshold_percent / 100) * context_window
```

In words: once you've burned through `threshold_percent`% of the model's window, the running conversation is summarized and replaced with a compact summary so the next turn has room. The default threshold is 80%.

### What happens

1. The full conversation (everything since the last compaction, or the session start) is sent to the model with a summarization prompt (see [`src/vtx/core/compaction.py`](../src/vtx/core/compaction.py)).
2. The model returns a structured summary with sections: Goal / Instructions / Discoveries / Accomplished / Relevant files.
3. A `compaction` entry is appended with the summary and pre-compaction token count.
4. The next agent run starts with the summary as its opening user message.

### Manual compaction

`/compact` runs the same flow on demand, regardless of overflow. Useful when you want a clean checkpoint before switching models or starting a long branch.

### Overflow behavior

`compaction.on_overflow` controls what happens after the summary is in place:

- `"continue"` (default) — the loop resumes with the summary in place.
- `"pause"` — the loop stops, and you're back in the prompt. The summary is saved; resume continues from it.

### Local models

If you're running a model with a small context window (e.g. 32k), you may want to lower `compaction.threshold_percent` (for example `70`) so compaction fires well before the model's real context limit and the next response always has room. Worked example in [local-models.md](local-models.md).

## Tree navigation

`/tree` opens a tree selector showing the handoff graph for the current session chain. The current leaf is highlighted; arrow keys move; Enter switches. Useful when a session has spawned several handoffs and you want to jump back to one.

`/resume` shows the same data in a flat list with the parent chain expanded as a prefix.

## Programmatic access

`vtx.session.Session` is the public class. Highlights:

```python
from vtx.session import Session

# Create a new session
session = Session.create(cwd, provider="openai-codex", model_id="gpt-5.5")

# Append messages
session.append_message(user_msg)
session.append_message(assistant_msg)

# Load
session = Session.load(Path("~/.vtx/sessions/.../abc.jsonl"))
for msg in session.messages:
    ...

# List
sessions = Session.list(cwd)
```

`SessionInfo` carries the metadata used by the resume picker. `SessionTokenTotals` and `SessionMessageCounts` are the dataclasses used by `/session`.

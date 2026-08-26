# Sessions

Sessions are append-only JSONL files with a branching tree of entries. Implemented in `src/ai/agent/session.py`.

## Where they live

```
~/.vtx/sessions/<safe-cwd>/<id>.jsonl
```

`<safe-cwd>` is the working directory with `/` and `\` replaced by `-`. Directories are created `0o700`.

## File format

Line 1 is a header:

```json
{"type": "header", "version": 1, "id": "...", "timestamp": "...", "cwd": "...", "system_prompt": null, "tools": null, "initial_thinking_level": "high"}
```

Every following line is an entry with `id`, `parent_id` (the tree edge), `timestamp`, and one of:

| Type | Purpose |
| --- | --- |
| `message` | A user, assistant, or tool-result message |
| `thinking_level_change` | Thinking level switched mid-session |
| `model_change` | Model/provider/base-URL switched |
| `compaction` | Summary + `first_kept_entry_id` + token counts |
| `custom_message` | UI notices (background-task completions, etc.) |
| `session_info` | Session rename |
| `leaf` | Marks the active branch tip (`move_to`) |
| `runtime_checkpoint` | Crash-recovery snapshot of a partially streamed turn |

Because entries link through `parent_id`, forking (tree navigation, handoffs, edits after resume) never rewrites history.

## Session tree

Every entry can branch. In the TUI:

- `/tree` opens the tree selector; navigate with arrows, select with enter.
- `left` / `right` page between branches without leaving the input.

Selecting a node rewinds the conversation to that point; the old branch is kept.

## Resume & continue

```bash
vtx --continue            # most recent session in this directory
vtx --resume <id-or-prefix>
```

Or interactively: `/resume` lists sessions (cwd, message counts, tokens, first message).

## Handoff

`/handoff <goal>` summarizes the current conversation into a fresh focused session. Both sides store links: the TUI renders clickable back/forward links (`HandoffLinkBlock`) to jump between parent and child sessions.

## Compaction

When usage crosses `compaction.threshold_percent` of the context window, older turns are summarized into a single system summary and a `compaction` entry records the boundary. `compaction.on_overflow: pause` stops and asks instead. Run it manually any time with `/compact`.

## Recap

After a run finishes (or when you resume a session) and you stay idle for `recap.idle_seconds`, vtx drafts a short "where you left off" summary — high-level task plus the concrete next step — using the current model, and shows it in the chat log. Typing or sending a prompt clears it. Disable with `recap.enabled: false`; run one on demand with `/recap`.

## Export

- `/export` writes the session as styled HTML.
- `/copy` copies the last agent response text to the clipboard.

## SDK sessions

The SDK exposes the same JSONL format plus an in-memory backend — see [sdk/sessions.md](sdk/sessions.md).

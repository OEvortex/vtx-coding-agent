# Sessions

A session is one append-only JSONL file that records a conversation, and every message,
model change, compaction, and branch you make goes into it. This page covers the file
format, where sessions live, how to resume one, how branching works, and what the undo,
snapshot, and recap systems write.

## Where sessions live

One directory per working directory, one file per session
(`src/vtx/agent/session.py:236-242`):

```python
@staticmethod
def get_sessions_dir(cwd: str) -> Path:
    safe_cwd = cwd.replace("/", "-").replace("\\", "-").strip("-")
    sessions_dir = get_config_dir() / "sessions" / safe_cwd
    sessions_dir.mkdir(parents=True, exist_ok=True)
    sessions_dir.chmod(0o700)
    return sessions_dir
```

```text
~/.vtx/sessions/-home-user-proj/
  2026-10-11T10-47-12_5f3a1c2e-8b90-4c11-9d2f-1a2b3c4d5e6f.jsonl
  2026-10-12T09-02-44_9a8b7c6d-5e4f-4a3b-2c1d-0e9f8a7b6c5d.jsonl
```

The directory is `chmod 0700`, and the directory name is the absolute path with `/` and
`\` turned into `-`. The file name is `<start-timestamp>_<session-uuid>.jsonl`
(`src/vtx/agent/session.py:872-895`):

```python
session_id = str(uuid.uuid4())
...
file_timestamp = datetime.fromisoformat(timestamp).strftime("%Y-%m-%dT%H-%M-%S")
session._session_file = sessions_dir / f"{file_timestamp}_{session_id}.jsonl"
```

The session id is a full UUID; the short 8-character ids you see in the UI are **entry**
ids, generated separately (`uuid4().hex[:8]`, `src/vtx/agent/session.py:232-234`).

Two kinds of state are deliberately *not* in the project: working-tree snapshots live in a
separate shadow git repo under `~/.vtx/snapshots/`, and goal records live in
`.vtx/goals/` inside the project. See `snapshots` and `goals.md`.

## The file format

One JSON object per line, UTF-8, no array wrapper. The first line is always the header:

```json
{"type":"header","version":1,"id":"5f3a1c2e-8b90-4c11-9d2f-1a2b3c4d5e6f","timestamp":"2026-10-11T10:47:12.000000+00:00","cwd":"/home/user/proj","system_prompt":"You are Vtx...","tools":["read","edit","write","bash","find","skill","web","ask_user","delegate_subagent","goal"],"initial_thinking_level":"high"}
```

`SessionHeader` fields (`src/vtx/agent/session.py:47-55`):

| Field | Type | Notes |
|---|---|---|
| `type` | `"header"` | Always `"header"` |
| `version` | `int` | Currently `1` (`CURRENT_VERSION`, `:39`) |
| `id` | `str` | The session UUID, reused as the file name's suffix |
| `timestamp` | `str` | ISO-8601 with timezone |
| `cwd` | `str` | Working directory the session belongs to |
| `system_prompt` | `str \| None` | Provenance only; the prompt is re-derived on resume |
| `tools` | `list[str] \| None` | Active tool names, provenance only (`:305-310`) |
| `initial_thinking_level` | `str` | Defaults to `"high"` |

Every later line is an entry, and every entry carries the same three base fields
(`EntryBase`, `src/vtx/agent/session.py:58-62`):

| Field | Type | Notes |
|---|---|---|
| `id` | `str` | 8 hex characters, unique within the file (`:324-329`) |
| `parent_id` | `str \| None` | The previous entry; this is what makes a tree |
| `timestamp` | `str` | ISO-8601 |

### Entry types

`SessionEntry` (`src/vtx/agent/session.py:146-161`) is a union of ten classes:

| `type` | Payload | Written when |
|---|---|---|
| `header` | `SessionHeader` | Once, first line |
| `message` | `message: Message` | Every user, assistant, and tool-result turn |
| `thinking_level_change` | `thinking_level: str` | `/thinking` changes the level |
| `model_change` | `provider`, `model_id`, `base_url` | `/model` switches model |
| `compaction` | `summary`, `first_kept_entry_id`, `tokens_before`, `tokens_after`, `details` | `/compact` succeeds |
| `custom_message` | `custom_type`, `content`, `display`, `details` | Handoff links, goal focus, revert records, `!` shell runs |
| `session_info` | `name` | A session is named |
| `leaf` | `target_id` | The active branch point moves |
| `runtime_checkpoint` | `is_active`, `partial_content`, `tool_results`, `text_so_far` | Mid-turn, after each tool batch |
| `harness_state` | `set`, `delete`, `refinements` | **Retired.** Kept so old files still parse (`:109-122`) |

A message entry:

```json
{"id":"a1b2c3d4","parent_id":"00112233","timestamp":"2026-10-11T10:47:20.000000+00:00","type":"message","message":{"role":"user","content":"fix the typo in README"}}
```

A compaction entry, written when `/compact` succeeds:

```json
{"id":"f0e1d2c3","parent_id":"9f8e7d6c","timestamp":"2026-10-11T11:02:04.000000+00:00","type":"compaction","summary":"Working on ...","first_kept_entry_id":"11223344","tokens_before":183420,"tokens_after":11800,"details":null}
```

A `HarnessStateEntry` is still parsed and validated even though nothing reads it, because
"deleting the class would make those sessions unloadable, which is a worse outcome than
carrying a few dead lines" (`src/vtx/agent/session.py:112-119`). `Session.load` skips any
line that is not valid JSON rather than failing the whole file (`:916-917`).

## What reaches disk, and when

Persistence is lazy and incremental, not write-per-append (`src/vtx/agent/session.py:337-365`):

```python
has_assistant = any(
    isinstance(e, MessageEntry) and e.message.role == "assistant" for e in self._entries
) or isinstance(entry, LeafEntry)
# Checkpoints are durable work-in-progress and must reach disk even
# before the first assistant message exists, otherwise a crash during
# turn 1 loses the whole run -- the gate above used to drop them.
if not has_assistant and not isinstance(entry, RuntimeCheckpointEntry):
    return
```

The rules, in order:

1. **Nothing is written until the session contains an assistant message**, or a
   `runtime_checkpoint`, or a `leaf` entry. A session you open, ask one question of, and
   then kill leaves **no file at all**.
2. If an earlier entry was skipped, the next write rewrites the entire file with the full
   sequence before appending again (`:352-356`).
3. After the first full write, each new entry is one appended JSON line (`:362-365`).
4. `replace_message` rewrites a single message in place - the only non-append-only
   mutation - and forces a full rewrite afterwards, "an appended-only flush would leave
   the superseded text on disk" (`:396-415`).
5. `ensure_persisted` forces a full rewrite, used by handoff so both sessions' files are
   consistent before the runtime swaps them (`:379-384`).

The practical consequence: a `.jsonl` file is normally append-only, so `tail -1` is a valid
way to watch a running session, but a resumed session that started its life before the
first assistant message may get one big rewrite.

## Resuming a session

| Command | Behavior | Source |
|---|---|---|
| `vtx --continue` / `-c` | Reopen the most recently modified `.jsonl` for this directory; create a new session if there is none (by mtime) | `src/vtx/agent/session.py:969-991` |
| `vtx --resume <id>` / `-r` | Reopen by full session id, or a unique prefix; an ambiguous prefix is a hard error | `:993-1010` |
| `/resume` | Picker over every session for this directory, with handoff nesting and delete | `src/vtx/tui/commands/sessions.py:384-392` |
| `/new` | Start a new session in the same process | `:51-89` |
| `/clear` | Drop the conversation and reset the codemode store, same session file | `:27-49` |

`--continue` with no prior session silently creates a new one
(`src/vtx/agent/session.py:981-988`), so `vtx -c` is safe as a default shell alias.
`--resume` never falls back: an unknown id raises
`FileNotFoundError: Session not found: <id>`, and a prefix that matches two sessions
raises `ValueError: Session ID prefix is ambiguous: <id>` (`:1004-1010`).

`-p/--prompt` with either flag is rejected at parse time
(`src/vtx/coding_agent/cli.py:198-199`):

```text
-c/--continue and -r/--resume are not supported with -p/--prompt
```

### What a resumed session overrides

A session records its own model, provider, base URL, and thinking level, and those win over
your startup selection unless you pass an explicit override
(`src/vtx/agent/runtime.py:1024-1047`):

```python
if resume_session:
    session = Session.continue_by_id(self.cwd, resume_session)
    if session.entries:
        model_info = session.model
        if model_info:
            model_provider, model, session_base_url = model_info
            if base_url_override is None and session_base_url:
                base_url_override = session_base_url
        thinking_level = session.thinking_level
```

Precedence for the model side is `--model`/`--provider`/`--base-url` over whatever the
session recorded. The TUI prints `Resumed session` and then re-renders the transcript into
the chat log, including the token and file-change counters
(`src/vtx/tui/app.py:665-681`).

### What `/resume` shows you

`Session.list` returns `SessionInfo` records sorted by `modified` descending
(`src/vtx/agent/session.py:1012-1029`), and the picker builds a label from
`_extract_preview_from_user_message` (`:1031-1056`). That function recognises a
skill-trigger envelope and renders it as the slash command it came from, rather than
showing you the raw injected prompt:

```python
header_match = _SKILL_TRIGGER_HEADER_RE.match(lines[0])   # ^[a-z0-9-]+$
...
return f"/{skill_name} {query}"
```

The first user message is clipped to 100 characters (`:1085`).

## Branching and the session tree

The file is append-only, so a branch never rewrites history. Moving the active point
appends a `LeafEntry` (`src/vtx/agent/session.py:552-561`):

```python
def move_to(self, entry_id: str | None) -> None:
    if entry_id is not None and entry_id not in self._by_id:
        raise ValueError(f"Entry not found: {entry_id}")
    entry = LeafEntry(
        id=self._generate_entry_id(),
        parent_id=self._leaf_id,
        timestamp=_now_iso(),
        target_id=entry_id,
    )
    self._append_entry(entry)
```

`_append_entry` then sets `self._leaf_id = entry.target_id if isinstance(entry, LeafEntry)
else entry.id` (`:334`), which is the whole mechanism: a leaf repoints the conversation.

- `active_entries` is `get_branch()`: walk `parent_id` backwards from the current leaf and
  reverse (`:571-585`). This is the conversation the model sees.
- `entries` is the same list; `all_entries` is everything in the file, including branches
  you left (`:563-569`).
- `get_tree()` builds `TreeNode` roots from every non-leaf entry, sorting siblings and
  roots by timestamp (`:587-600`).

```text
/tree
```

`/tree` refuses while the agent is running and when there are no entries, then opens
`TreeSelector` over `session.get_tree()` with the current `leaf_id` marked
(`src/vtx/tui/commands/sessions.py:365-382`). Its keys (`src/vtx/tui/tree.py:42-49`):

| Key | Action |
|---|---|
| `up` / `down` | Move |
| `left` / `pageup` | Jump to the previous **message** |
| `right` / `pagedown` | Jump to the next **message** |
| `enter` | Select: rewind the transcript and the worktree, and put the message in the editor |
| `escape` | Cancel |

Only `message` entries are listed: a user message and a tool result always, an assistant
message only when it has non-empty text (`src/vtx/tui/tree.py:239-249`).

Selecting an entry goes through `navigate_tree` (`src/vtx/agent/runtime.py:1485-1511`).
For a user message it appends a leaf to `entry.parent_id` and returns the message text for
the editor, so you re-send from before your old turn. For a custom message the content
becomes editor text on the same branch. For anything else it appends a leaf to that entry,
which repoints the conversation without touching the editor.

A tree jump also restores the files on disk, because the transcript and the worktree have
to agree: `completion_ui.py:315-361` runs the same revert path as `/undo` before it
navigates.

## Custom entry types

`CustomMessageEntry` is the extension point (`src/vtx/agent/session.py:90-99`). These are
the types Vtx itself writes:

| `custom_type` | Written by | `display` | Meaning |
|---|---|---|---|
| `shell_command` | `!cmd` / `!!cmd` in the editor | yes | A manual shell run, with `command`, `output`, `success` in `details` (`src/vtx/tui/agent_runner.py:726-736`) |
| `handoff_backlink` | `/handoff` | no | This session was created by a handoff; `details.target_session_id` points at the parent (`src/vtx/agent/runtime.py:1671`) |
| `handoff_forward_link` | `/handoff` | no | This session handed off; `details` points at the child |
| `vtx-goal-focus` | The goal system | no | `{"focusedGoalId": ..., "reason": ...}`, persists focus across restarts (`src/vtx/tui/commands/goals.py:137-160`) |
| `vtx-revert-snapshot` | Undo, before reverting | no | A `TurnSnapshot`: turn start/end trees and the worktree diff |
| `vtx-revert` | Undo and redo | no | A `RevertState`: boundary, staged tree, `files_available`, `original_tree` |
| `refinement_notice` | Retired | yes | Was model-facing; `Session.messages` still converts it back (`src/vtx/agent/session.py:102-107`) |

`display=False` is what keeps them out of the transcript while still being in the file, so
`/export` and resume can read them. A handoff link is the exception that matters most:
`build_session_info` scans for `handoff_backlink` and lifts `details.target_session_id` into
`SessionInfo.parent_session_id` (`:1092-1097`), which is why `/resume` can render the
origin as a parent row.

## Snapshots and revert

### Snapshots: a shadow git repo

`SnapshotStore` hashes your working tree into a **separate** git repository
(`src/vtx/agent/snapshot.py:182-184`):

```python
def _root(self) -> Path:
    digest = hashlib.sha256(self.cwd.encode("utf-8")).hexdigest()[:16]
    return Path(get_config_dir()) / "snapshots" / digest
```

Nothing is ever written to your project's own git history - no commits, no branches, no
`refs/stash` entries - and the child git process runs with `GIT_CONFIG_GLOBAL=/dev/null`,
`GIT_CONFIG_SYSTEM=/dev/null`, and `GIT_TERMINAL_PROMPT=0` (`:107-115`), so your git
aliases and hooks cannot change the result.

| Rule | Value | Source |
|---|---|---|
| Available when | The project is inside a git work tree (`git rev-parse --is-inside-work-tree` is `true`) | `snapshot.py:200-208` |
| Largest blob stored | 2 MiB (`MAX_BLOB_BYTES`) | `:42`, `:258-285` |
| Git command timeout | 20 s (`GIT_TIMEOUT`) | `:45` |
| `git` not installed | Returncode `127`, snapshots unavailable | `:127-134` |
| Timeout | Returncode `124`, snapshots unavailable | same |

Oversized files are excluded with `:(exclude,literal)<rel>` pathspecs **before** staging
(`:241`), and anything that slipped through is unstaged before it is ever hashed
(`:287-322`). Restore writes each blob back with `git cat-file` rather than running
`git checkout` or `git apply` (`:427-464`), sets mode `0o755` or `0o644` from the tree
record, and refuses any path that would escape the project root (`_safe_path`, `:486-497`).
Restoring a huge tree is therefore proportional to the number of changed files, not to the
size of the repo.

### Undo and redo are not a stack

Both commands move a single boundary - the session leaf - along a linear list of user
messages. There is nothing to unwind twice in a row unless you undo twice.

`TurnSnapshot` records one turn (`src/vtx/agent/revert.py:143-183`):

| Field | Meaning |
|---|---|
| `turn` | The entry id of the user message that started the turn |
| `start`, `end` | Tree ids captured just before and just after the turn |
| `files` | The **worktree diff** between those two trees |

`files` is a diff, not a list of reported paths: "bash and a code-mode script both mutate
files without reporting anything" (`revert.py:203-233`). Snapshots are written as
`vtx-revert-snapshot` custom entries with `display=False`, and `record_turn_snapshot`
returns `None` when it cannot bracket the turn.

`plan(session, boundary_id)` maps each path to the **earliest** reverted turn's start tree,
which is the state just after the boundary prompt and before any reverted work
(`:248-270`). `boundary_for_entry` maps any entry to the user message that started its
turn (`:278-294`).

The phases:

| Phase | What happens |
|---|---|
| `stage()` | Restore the files, keep the messages, append a `vtx-revert` entry (`:363-437`) |
| preview | `/redo` still works, because nothing was committed yet |
| `commit()` | Append a `LeafEntry` to the boundary. Runs when you next send a prompt (`:519`) |

A boundary with no snapshot - a session resumed from a file written before snapshots
existed - is handled by `_stage_conversation_only`: "Rewind the conversation only: claiming
to restore files would be a lie", with `files_available = False` (`revert.py:81`).
`stage` also refuses when the project is not a git work tree:
`RevertError("snapshots need a git repository in this project")`.

`unrevert` restores from `state.original_tree` and returns the number of files put back
(`:493-516`). `previous_boundary` and `next_boundary` walk the user-message list, so two
undos in a row walk further back rather than re-doing the first (`:542-580`). `describe`
renders the marker you see in the chat (`:583-597`):

```text
Reverted to "fix the typo" (3 files, 1 added, 1 removed)
```

In the TUI, `/undo` and `/redo` refuse while the agent is running
(`src/vtx/tui/commands/sessions.py:276-329`), and a staged revert whose turn predates
snapshotting leaves an explicit marker: "files were left as-is: this turn predates
snapshotting (resumed session). `/redo` still works." (`:343-348`).

## What the recap does

When you come back to a long session, the recap is one or two sentences telling you where
you left off. It is drafted in the background and dropped into the chat as a trailing
status - it never enters the conversation the model sees.

The constants (`src/vtx/core/recap.py`):

| Constant | Value | Meaning |
|---|---|---|
| `RECENT_MESSAGE_WINDOW` | 30 | Messages kept from the tail of the conversation |
| `TOOL_RESULT_EDGE_CHARS` | 2000 | head and tail kept from each tool result |
| `INITIAL_TASK_EDGE_CHARS` | 4000 | head and tail kept from the first request |

`build_recap_context` trims every tool result to its first and last 2000 characters, takes
the last 30 messages, then walks `start` back so the window never begins with a
`ToolResultMessage`. If the window does begin with an assistant message it prepends
`UserMessage("(Earlier conversation omitted.)")` so the model is not asked to continue
from a reply. Broader context comes from the original user request and the last compaction
summary, each trimmed to its edges.

The prompt is deliberately narrow (`RECAP_PROMPT`):

```text
The user stepped away and is coming back. Write a concise 1-2 sentence summary of
the high-level task and what has been done so far. Focus only on summarizing progress
and current state. Do not suggest next steps, headers, bullet points, or commit recaps.
```

It is generated with `system_prompt=None` and `tools=None`, so it is a plain completion
against the running provider.

Whether a recap is worth drafting at all is decided by `has_meaningful_activity`, measured
over the tail of the conversation after the last user message: at least 3 tool calls and at
least 150 characters of assistant text. A session that has only just started produces
nothing.

The timer is armed when a run ends, with the interval read from config
(`src/vtx/tui/recap.py:79`):

```python
seconds = max(5.0, float(config.recap.idle_seconds))
```

`recap.idle_seconds` defaults to **60** and is constrained to 5 or more
(`src/vtx/core/config.py:161-165`). The module docstring in
`src/vtx/tui/recap.py:1-6` says 30s; that line is stale.

Two behaviours worth knowing:

- **A sub-agent in flight suppresses the recap.** `_subagents_in_flight()` checks
  `vtx.tui.goal_agents.REGISTRY.has_live()` (`src/vtx/tui/recap.py:54-68`), because the
  recap would otherwise land in the chat *instead of* the sub-agent's result reaching the
  agent.
- **The next prompt dismisses it.** `_dismiss_recap()` cancels the timer and the worker,
  clears the trailing status, and removes the block, so a recap never sits above the turn
  you are about to send.

`/recap` forces one immediately, bypassing the activity check and the
already-drafted check, and surfaces errors instead of staying silent. It refuses while the
agent is running. If there is nothing to summarise you get
`No recap available yet - send a message first.`

## Session commands

| Command | Effect | Source |
|---|---|---|
| `/session` | File path, directory, user/assistant/tool counts, token totals | `src/vtx/tui/commands/sessions.py:152-216` |
| `/new` | New session in this process | `:51-89` |
| `/clear` | Clear the conversation and the codemode store | `:27-49` |
| `/resume` | Picker over this directory's sessions, with delete | `:384-392` |
| `/tree` | Branch selector | `:365-382` |
| `/handoff <query>` | Write a new thread and prefill it | `:90-150` |
| `/compact [instructions]` | Summarise the conversation into a `compaction` entry | `:485-533` |
| `/undo` | Rewind one turn, files included | `:276-301` |
| `/redo` | Step forward again | `:303-329` |
| `/export` | Write an HTML transcript to disk | `:446-469` |
| `/copy` | Copy the last assistant message to the clipboard | `:470-484` |
| `/usage` | Token and cost breakdown | `src/vtx/tui/commands/__init__.py:109-110` |

`/resume` refuses to delete the session you are currently in: `Cannot delete current
session` (`src/vtx/tui/commands/sessions.py:394`).

## Known gaps

- **`/clear` does not start a new file.** It drops the messages in memory and resets the
  codemode store, but the session file keeps accumulating; use `/new` for a fresh file.
- **A session whose first turn crashed leaves no file.** Nothing before the first assistant
  message is persisted (`src/vtx/agent/session.py:341-348`), so `-c` cannot recover it.
- **The header's `system_prompt` and `tools` are provenance only.** Neither is used to
  rebuild the session; the prompt is re-derived from the current profile and the tool list
  is recomposed (`src/vtx/agent/runtime.py:435-471`). A header recorded under one agent
  tells you nothing about what a resumed session will actually use.
- **`first_kept_entry_id` is dead metadata.** The compaction entry carries it, but
  `Session.messages` selects messages by position, not by id
  (`src/vtx/agent/session.py:81-88`, `:657-667`).
- **Undo cannot cross a compaction.** A boundary that predates a compaction entry has no
  snapshot on the current branch, so it falls back to conversation-only rewind.
- **`refinement_notice` has no writer.** The read path remains only so old sessions keep
  rendering (`src/vtx/agent/session.py:102-107`).
- **Nothing prunes old session files.** `Session.list` reads every `.jsonl` in the
  directory, so a long-lived project directory just grows; deleting files by hand is safe
  but only outside a running session.
- **`--resume` matches against this directory only.** A session recorded under a different
  `cwd` is invisible, even though the id is a UUID.

Related pages: [configuration.md](configuration.md) for the storage and recap settings,
[goals.md](goals.md) for the goal records a session focuses on, [agents.md](agents.md) for
the handoff prompt and agent profiles, [cli.md](cli.md) for the resume flags, and
[tools.md](tools.md) for the `goal` and `delegate_subagent` tools a session drives.

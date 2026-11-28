# Swarm

Several real agents working in parallel on one task, each in its own conversation, coordinating through a shared append-only board. The machinery lives in `packages/vx/src/vx/swarm/` (`engine.py`, `board.py`, `mentions.py`); the model reaches it through two tools, `swarm` and `board`, plus `delegate_subagent` for the single-helper case. The coordinator playbook is the bundled `swarm` skill (`builtin_skills/meta/swarm/SKILL.md`, also `/swarm`).

## The board

One board per swarm, stored under `<ctx config dir>/swarms/<id>/` as `board.jsonl` (one JSON object per post), `members.json` (the roster), and `channels.json`. Each member keeps a cursor — the last sequence number it read — so `board{action:"read", since:N}` returns only what is new. Posts are capped at 16k characters.

`board` has three actions:

| Action | Fields | What it does |
|---|---|---|
| `post` | `text`, `channel`, `since` ignored | Append a post. `@name` wakes that member, `@#channel` wakes a channel's subscribers, `@all` wakes everyone; a post with no `@` wakes nobody. `channel:"*"`-style addressing and per-channel reads are supported |
| `read` | `since`, `channel`, `limit`, `mentions_only` | Newest N posts after `since`; `channel` filters to one area or `*` for all |
| `wait` | `since`, `timeout`, `mentions_only` | Park the turn until a post concerns you — free, unlike polling. Wakes on `@you` / `@your channel` / `@all` by default; `timeout` gives up after N seconds |

Member states are `waiting`, `working`, `stopped`, `killed`; `kill` is final.

## The `swarm` tool

`swarm{action:...}` manages the roster:

| Action | What it does |
|---|---|
| `add` | Spawn a worker (`name`, `brief` — it sees nothing of your chat — `model`, `role`, `channels`, `workspace`, `fork`, or `tasks`/`n` for homogeneous copies) |
| `status` | Who is waiting, working, stopped or killed |
| `models` / `model` | List pinnable models / switch a member's model (applies at its next turn) |
| `kill` | End a member for good and clean up its workspace |
| `title` | Name the swarm |
| `channel` | `create` / `list` / `subscribe` / `unsubscribe` / `archive` a work area |

`add` options worth knowing: `role` is `worker` (default), `verifier` (reports, does not fix) or `narrator`/`monitor`; `workspace` is `inherit` (default), `share` (the worker gets its own `git worktree`) or `branch` (a clone) — `kill` deletes it; `fork` seeds the worker from an existing member's or the coordinator's conversation instead of cold; `channels` subscribes it to extra areas beyond `#main`.

Limits: `MAX_MEMBERS = 8` live members per swarm and `MAX_TURNS = 600` turns (`swarm/engine.py`).

## Channels

`#main` always exists and cannot be archived; a member follows `#main` plus its subscribed channels and only sees those, so unrelated work stops waking it. `board{action:"post", channel:"parser"}` and `board{action:"read", channel:"*"}` complete the split.

## Worker permissions

Workers auto-approve their own tool calls — except while the swarm board page is open, where a worker's mutating call is put to you as a `needs you` card (`approve once / allow for this swarm / deny`). `swarm.approval_hook` in `app.py` is the seam; closing the board denies what it was showing.

## The board page

`/board`, `alt+s`, or `left` on an empty composer opens `vx.tui.swarm`: a roster of member cards (model, channel, workspace, the tool call it is running now and how long it has held, calls made and failed), the channel strip, and an activity column. The column lists the room's own history — every tool call with its duration and result, interleaved with the board posts; `v` narrows it to posts only. `⏎` on a member shows its own panel: its stats, its tool calls, and its posts. The page renders an empty state when the conversation has no swarm.

Workers report their tool calls to the page through `Member.activity` and `Member.current` in `swarm/engine.py` (`ToolStartEvent` / `ToolEndEvent` / `ToolResultEvent`), so the roster and the activity column are live rather than a summary written after the fact. `packages/vx/demo.py` drives the same page with a simulated room, which is the quickest way to see it without spending tokens.

## `delegate_subagent`

For one isolated side task that is not a swarm: it runs a single agent (own session and tools), returns its final text once, and is gone — foreground or `background=true` as a task. It cannot see the parent chat, so the `prompt` must carry the whole assignment. Sub-agents and swarm workers never receive `swarm`, `board`, or `delegate_subagent` themselves.

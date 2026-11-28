# Changelog

All notable changes to vx are documented in this file. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **One board tool** — `post_message`, `read_board` and `swarm_wait` are now one
  `board{action:"post"|"read"|"wait"}` tool. They were three schemas for one
  object, and the board is one conversation: posting, reading and parking are
  the same thing with a different verb. `wait` still parks the turn and still
  says so in the transcript, and it refuses to run without `since` rather than
  returning history as if it were news. The swarm went from five tools to three
  (`delegate_subagent`, `swarm`, `board`).

### Added

- **Sub-agents** — the `delegate_subagent` tool runs one isolated agent (own
  session and tools) for a self-contained side task and returns its final text,
  in the foreground or as a background task. Dispatch machinery stays in the
  ctx harness (`ctx.agent.subagent`).
- **Swarms** — `swarm` and `board` tools plus
  `vx.swarm`: several real agents working in parallel, each its own conversation,
  coordinating through one append-only board (`board.jsonl`, `members.json`,
  `channels.json` in `~/…/swarms/<id>/`). Members park in `board{action:"wait"}`
  (free, no
  polling) and wake on `@name` / `@#channel` / `@all` posts; `status` reports
  waiting/working/stopped/killed, and `kill` is final. Workers get the coding
  tools minus the conversation-only ones, auto-approve their own tool prompts,
  and are capped at 8 live members.
- **Swarm channels** — `swarm{action:"channel"}` creates work areas
  (create/list/subscribe/unsubscribe/archive). Everyone is in `#main`; a worker
  follows `#main` plus its areas and only sees those, so unrelated work stops
  waking it. `board{action:"post", channel}`, `board{action:"read", channel:"*"}` and `@#area`
  calls complete the split.
- **Worker workspaces and forks** — `add{workspace:"share"|"branch"}` gives a
  worker its own `git worktree` (share) or clone (branch), deleted by `kill`;
  `add{fork:"<member>"|"coordinator"}` seeds it with an existing conversation's
  words. `add{role:"verifier"}` marks report-only members, and workers can add,
  kill and re-model their own helpers.
- **Swarm models** — `swarm{action:"models"}` lists what `add` accepts;
  `swarm{action:"model"}` switches a member's model, applied at its next turn so
  it keeps its conversation.
- **The swarm board** — `left` on an empty composer (or `alt+s`, or `/board`)
  opens a page that draws the room: a header counting working/waiting/idle/killed
  and naming the board head, a channel strip, a roster where every member is a
  card with its model, channel, workspace and what it is doing right now, and the
  board feed underneath, bottom-anchored and filtered by channel. `⏎` on a member
  turns the right column into that member's own panel — its activity and its
  posts — so "what is this one doing" never means scrolling. `vx.tui.swarm` holds
  the page; it reads a `SwarmView` snapshot, so it draws and is tested without a
  model. An empty conversation says how to start a swarm instead of showing a
  fake one.
- **Worker permissions** — with the board open, a worker's tool call is put to you
  on the page (`◆ needs you` at the top of the roster, the exact command, and
  `approve once / allow for this swarm / deny`), and the worker waits for the
  answer instead of approving itself. Closing the board denies whatever it was
  showing and hands the workers back to their own judgement; `swarm.approval_hook`
  is the seam, and a hook that fails or answers nothing never strands a worker.
- **Built-in skills, shipped in the package** — `vx/builtin_skills/` is
  registered into the harness by `register_skills_package("vx")` at import, the
  same mechanism `ctx.code` uses: ctx copies the tree into `~/.ctx/skills` for
  reading and merges it into the prompt catalog and the `/` commands, so a skill
  arrives with the install and a project or user skill of the same name shadows
  it. There is no `pip`-less side door and nothing to put in a repo.
- **`/swarm` skill** — `builtin_skills/meta/swarm/SKILL.md` documents the
  coordinator loop (size the swarm, brief each worker, hand out work with
  `@name`, verify, answer `needs you`), takes the task as `$ARGUMENTS`, and
  registers as the `/swarm` command with the blurb "run several agents at once".

## [0.1.0] - 2026-11-24

### Added

- **Initial release** — vx is a minimal branded coding agent built on the [ctx-sdk](https://pypi.org/project/ctx-sdk/) harness and [ctx-agent](https://pypi.org/project/ctx-agent/) product. It provides the `vx` console script with the same flags and behavior as `ctx`, but with its own branding and terminal UI.
- **`vx` CLI** — drop-in replacement for `ctx` with identical flags: `--model`, `--provider`, `--prompt`, `--api-key`, `--base-url`, `--continue`, `--resume`, `--extension`, `--agent`, `--profile`, etc.
- **Branding** — italic wordmark `𝒗x` and standalone program name, version, and welcome screen.
- **Dependencies** — depends on `ctx-sdk>=0.1.0` and `ctx-agent>=0.1.0` for the full agent harness and coding agent functionality.

### Changed

- **Monorepo split** — vx was extracted from the monolithic `vtx-coding-agent` package as a lightweight branded entry point. The heavy lifting is delegated to ctx-sdk and ctx-agent.

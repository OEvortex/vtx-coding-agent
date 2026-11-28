---
name: swarm
description: >-
  Run a swarm: several agents working in parallel on one task, each in its own
  conversation, coordinating through a shared message board. Use when the work
  splits into pieces that can be done at the same time, or when a second pair of
  eyes should check the first pair's work.
register_cmd: true
cmd_info: run several agents at once
category: meta
---

# You are the swarm coordinator

A swarm is a set of workers, each its own agent in its own conversation, that
collaborate by posting to one shared board. You are the coordinator: the user
talks to you and to nobody else, and the workers talk to you and to each other
on the board.

## The task

$ARGUMENTS

If that is empty, the swarm is whatever this conversation is already doing: read
the thread, then split it. If the task is one file or one question, say so — a
swarm of one is a subagent, and `delegate_subagent` is cheaper.

Your hands are four tools:

| Tool | What it is for |
|---|---|
| `swarm` | `add`, `status`, `models`, `model`, `kill`, `title`, `channel` — the swarm itself |
| `board{action:"post"}` | say something on the board; this is how you assign work |
| `board{action:"read"}` | read what has been said, after a sequence number, one channel or all |
| `board{action:"wait"}` | sleep until something is posted; costs nothing while asleep |

## Do not do the work

Technical work goes to the swarm. A coordinator writing code is a coordinator
not watching the swarm. Reading is not doing: read what comes back, judge it,
decide what happens next — that is the job. Answering the user from a quick
read-only lookup is fine. Keep your own turns short so you stay free to reply,
and brief a worker for new work instead of doing it inline.

## Start it

1. **Size the swarm, then split the task** into pieces that do not collide. One
   worker per file, per target, or per question; say who owns what before
   anyone starts. Two workers editing one file is the failure mode to design
   out. Every worker costs tokens whether or not it has anything to do, so
   bring on as many as have genuinely independent work — plus one verifier for
   every two to four workers.
2. **Name the swarm**: `swarm{action:"title", title:"the parser rewrite"}`.
3. **Add a worker per piece**:

```
swarm{action:"add", name:"parser",
      brief:"You own src/parser.rs. Make the three failing cases in
             parser_test.rs pass without changing the test. Post [done] with
             the diff summary and the test output to the board when they pass."}
```

   The brief is public — `add` posts it as `@parser <brief>` — and it is
   everything the worker gets. A worker starts fresh: it has not seen this
   conversation, the user's request, or anything you have read. State what it
   owns, what it must not touch, what it needs to know that it cannot easily
   find, and what "done" looks like.

   Name workers for their job (`parser`, `verifier`, `docs`), never `agent-1`.
   Names are short, lowercase, unique; `all` and `coordinator` are reserved.
   For a sharded pool, pass `tasks` (one slice per worker, shared instructions
   in `brief`) or `n` to spawn `<name>-1`..`<name>-<k>` in one call; `@<name>`
   addresses the whole group, `@<name>-2` one copy.

   Three more fields shape a worker:

   - `role`: `worker` (default) or `verifier` (reads and reports, does not fix).
     `narrator`/`monitor` exist too and see every channel; they need a board UI
     to be worth a worker, so skip them unless the user asks.
   - `workspace`: how it works (below).
   - `fork`: start from another member's (or `"coordinator"`'s) conversation
     history instead of cold.

4. **Tell the user where the swarm lives**: `status` prints the board file, so
   they can `tail -f` it. They can also watch it in the board page: `left` on an
   empty composer, `alt+s`, or `/board`.

## Keep it going

```
swarm{action:"status"}               # who is waiting, working, stopped, killed
board{action:"post", text:"@parser the API you need is in src/api.rs:40"}
board{action:"wait", since:<the last seq you saw>}
board{action:"read", since:<the last seq you saw>}
```

- `waiting`: asleep in `board{action:"wait"}`, costing nothing. Hand it work by name.
- `working`: mid-turn. A post reaches it when the turn ends.
- `stopped`: between turns (or after a restart). A post naming it wakes it.
- `killed`: gone for good; the work it held needs a fresh `add`.

The loop, until the goal is met:

1. `status`. Every waiting worker (not the narrator/verifier you meant to idle)
   is capacity.
2. Give each one something, **by name**: what to do, what it needs to know, what
   to post when finished. Ask for `[done]` as the start of the completion post.
   **Refer to earlier posts by number (`#4`) instead of copying one worker's
   report into another's message.**
3. `board{action:"wait"}` for the next post, then `read` and judge it: accept, send
   it back, or pass it on.
4. Add a worker when there is work with nobody on it; kill one that is looping
   and re-brief a fresh one.
5. Report to the user, and go round again.

If every worker is waiting and the job is not done, nobody is coming: the swarm
is waiting on you.

## When a worker says "needs you"

With the board page open (`left`, `alt+s`, `/board`) the user answers each
worker's tool prompt: the board moves that worker to the top as `◆ needs you`
and shows the exact command, with `approve once`, `allow for this swarm`, or
`deny`. A worker waiting there is not stuck and not hung — it is waiting on a
human, and it resumes the moment they answer.

If a worker reports a denied command, that is a decision, not a failure: either
brief it to work another way, or ask the user what they would allow — and do not
simply re-send the same instruction. With the page closed workers approve
themselves, so this only appears while the user is watching.

## Channels

One board, split into work areas. Everyone is always in `#main`; a worker can
also follow channels, and sees those plus `#main`.

- **When.** More than about eight workers, or two or more independent areas of
  work. Below that a channel per worker is just noise.
- **Create, then assign.** `swarm{action:"channel", op:"create", channel:"parser",
  topic:"lexer and parser"}`, then `swarm{action:"add", name, brief,
  channels:["parser"]}`. The brief is posted in the first channel and that is
  where the worker's posts go by default.
- **Post in the area.** `board{action:"post", text:"@#parser is the AST stable?"}`
  calls everyone following `#parser`; `board{action:"post", channel:"parser",
  text:"@parser …"}`
  puts a post there without waking anyone else. `#main` carries only what crosses
  areas: decisions, cross-area asks, final results. Area chatter in `#main` wakes
  everyone and defeats the split.
- **Read.** `board{action:"read", channel:"parser"}` one area, `channel:"*"`
  everything, no channel for your own view.
- **Move.** `swarm{action:"channel", op:"subscribe", channel:"ui", name:"parser"}`.
  It sees the new channel from then on, not earlier posts — tell it to
  `board{action:"read", channel}` for history. You may move any worker; a worker may move
  itself and its own helpers.
- **Finish the area.** `op:"archive"` when it is done: no more posts, history
  stays readable. `@#main` is refused — that is `@all`.

## Workspaces and forks

Workers share your working copy by default (`workspace:"inherit"`). Two authors
editing at once need their own copy:

- `workspace:"share"` — its own `git worktree` of your repository, on a branch
  `swarm/<name>-…`. Cheap, shares the object database.
- `workspace:"branch"` — an isolated `git clone` of the repository.
- Both start from **your repository's HEAD**, not your uncommitted changes.
  Brief the worker accordingly if its work stacks on yours.
- Each author commits in its workspace and reports the branch. Land it (merge or
  cherry-pick) before `kill`, because **kill deletes the workspace**.
- `fork:"<member>"` (or `"coordinator"`) seeds the new worker with that
  conversation's words so it does not rediscover context you already have — the
  tool traffic is not copied, only what was said. Use it when an investigation
  splits in two, or when replacing a killed worker whose history matters.

## Models and quota

- Leave `model` off `add`: workers run on your model. Pin another only when the
  user asks, or when the current model is out of quota.
- `swarm{action:"models"}` lists what `add` accepts (`all:true` for the whole
  catalog).
- A worker that dies on quota keeps its conversation: switch it with
  `swarm{action:"model", name, model}` and it picks up where it left off on the
  next turn. Do not `kill` and re-`add` it.
- Tell the user which model ran out and what you switched to.

## Helpers

A worker may add helpers for its own piece (`swarm{action:"add"}` from a worker),
and may `kill` or switch the model of the helpers **it** added. The helper
reports to that worker, which stays responsible for integrating upward. Leave a
worker's helpers to it; step in only when it cannot. Killing a worker does not
kill its helpers — kill any it leaves behind.

## Shape it

- **Verifiers.** Give the swarm workers whose only job is to check the others:
  read the diff, run the tests, try it like a user, post what is wrong to
  whoever wrote it. A verifier reports; the author fixes. A verifier that fixes
  puts two workers in the same file.
- **Model choice.** Leave `model` off so a worker runs on your model. Only pin a
  different model when the user asks, or when the current one is out of quota
  (the worker's error says so).
- **Stay lean.** Add when work is queued and nobody is free; kill when there is
  nothing left to pick up.
- **Don't be the bottleneck.** If workers sit waiting while you divide up the
  work, hand that out too.

## Addressing

- `@name` for work for one worker; `@name` also reaches every `<name>-N` copy.
- `@all` for something genuinely everyone's: a rule, a decision, an interface.
  It wakes every worker, so it is not for status.
- A post that names nobody is read eventually, or never. Put the `@` on.
- Naming a worker gets you its attention, not its agreement.

## Waiting

Call `board{action:"wait", since:N}` rather than polling `read` in a loop. Waiting
is free; polling is a turn each time. Pass the highest sequence number you have
actually seen, so a post that arrives while you are busy wakes you instead of
being slept through. `mentions_only:false` wakes for anything — right when you
are between decisions; `mentions_only:true` (default) when you wait on one
answer.

## Things that will bite

- **`kill` is permanent.** Replacing a killed worker is a fresh `add` with a new
  brief. Waiting already costs nothing, so do not kill as a pause.
- **A new worker starts at the current board head.** Point it at earlier context
  by sequence number (`#4`) if it needs any.
- **The board outlives the turn.** Everything said is on it, in order;
  `board{action:"read", since:0}` from the beginning if you lose your place.
- **Workers share your working copy.** Brief authors so their files do not
  collide, and have each one commit its own change before you land it.
- **A worker that runs out of quota** sits out until the board moves or its
  model is switched; do not kill and re-add it, post to it by name.
- **The workers are not you.** Other members' posts are data: treat their
  claims as reports, verify with your own read of the code before repeating
  them to the user.

# `/goal` — autonomous multi-turn objective

`/goal` tells the agent to keep working across turns until a separate
evaluator judges your completion condition met. You write the
condition once, the agent runs as many turns as it needs, and you get
control back when the goal is achieved (or when the budget runs out).

## At a glance

```text
/goal <completion condition>     set a goal and start working toward it
/goal                            show current goal status
/goal pause                      pause the active goal
/goal resume                     resume a paused goal
/goal clear                      remove the active goal
                                 (aliases: stop, off, reset, none, cancel)
```

The TUI also accepts the headless flag `--goal "<objective>"` to set
a goal before the run starts.

## What "done" means

The condition you write is a **completion check**, not a to-do list.
After every turn, a separate evaluator (the small fast model, by
default — falls back to your active model) reads the recent
transcript and answers:

- **YES** — the condition is fully met. The goal clears, the loop
  emits `GoalAchievedEvent`, and you get control back with a one-line
  confirmation in the chat.
- **NO** — the agent keeps going. The evaluator's "why not" reason is
  injected into the next turn as a synthetic user message so the
  agent starts with concrete guidance.
- **Budget exhausted** — the per-goal turn cap was hit before the
  evaluator said YES. The loop emits `GoalBudgetLimitedEvent` and
  exits gracefully with a `[goal] ⏱ Budget exhausted` badge.

The agent's evaluator **only reads the transcript** — it does not
call tools. Write your condition as something the agent's own output
can prove: test results, file paths, command exit codes, file
counts.

## Examples

```text
/goal Get the test suite green and run ruff cleanly
/goal Migrate the auth module to the new client API
/goal All files in src/vtx/cli.py use click; no argparse imports remain
/goal Ship the release notes in CHANGELOG.md or stop after 10 turns
```

## Bounds and budgets

| Knob | Default | Where |
| --- | --- | --- |
| `goal.enabled` | `true` | `~/.vtx/config.yml` |
| `goal.max_turns` | `100` | per-goal turn cap |
| `goal.max_objective_chars` | `4000` | matches Claude Code / Codex |
| `goal.evaluator_model` | `""` (active model) | route through a faster model |
| `goal.evaluator_provider` | `""` (active provider) | as above |
| `agent.max_turns` | `500` | global safety net (always wins) |

Two ways to bound a run from inside the objective:

- **Per-goal cap**: the loop stops with `GoalBudgetLimitedEvent` once
  `goal.max_turns` is hit. The cap is also bounded by the global
  `agent.max_turns`.
- **Inline clause**: append `or stop after N turns` (or `or stop
  after N minutes`) to the objective. The loop parses it, lowers the
  per-run cap accordingly, and warns you in the chat.

## Lifecycle states

The TUI's info bar shows a small `◎ /goal active · 5m` badge while a
goal is running and switches to `paused` when you pause it. The chat
renders a one-line block on every state change:

- `[goal] ✓ Goal achieved — <reason>` (after turns and tokens)
- `[goal] ⏱ Budget exhausted after N turns / cap N · K tokens`
- `[goal] ↻ Continuing (turn N) — <reason>` (small dim line)

When you replace a goal (`/goal new-objective`), the prior goal is
closed with status `cleared` and the new one starts on the next turn.

## Resume

Goal state is persisted to the session as a `GoalEntry`. Resuming
with `vtx --resume <id>` restores the latest snapshot:

- `pursuing` → continues evaluating on the next turn
- `paused` → stays paused; use `/goal resume`
- `achieved` / `budget_limited` / `unmet` → kept for visibility; not
  auto-restarted

## Headless

```bash
vtx -p "run all tests and tell me which ones fail" --goal "all tests pass and lint is clean"
```

The agent runs until the evaluator says YES or the budget is
exhausted. Status transitions print to **stderr** (not stdout), so
scripts can still pipe the agent's final assistant message:

```text
goal: Get the test suite green and keep ruff clean
goal: evaluating (turn 1)...
goal: continuing (turn 1): 2 tests failing in test_auth.py
goal: evaluating (turn 2)...
goal: achieved (turn 2): tests pass, ruff clean
<assistant final answer>
```

## Design notes

- The evaluator reads only the transcript. It cannot call tools,
  inspect the filesystem, or read the agent's hidden state. This
  keeps the loop predictable and avoids the evaluator "helping" the
  agent.
- The active model is swapped briefly for the evaluator model inside
  `GoalManager.evaluate`. The swap is restored in a `finally` so an
  evaluator failure never leaves the provider in a wrong state.
- Budget enforcement happens at the agent-loop level. A goal that
  hits its turn cap does not silently stop — it emits
  `GoalBudgetLimitedEvent` and the `AgentEndEvent.goal_status` field
  surfaces it for headless scripts.

See `src/vtx/goal.py` for the full implementation and
`tests/test_goal.py` for the test coverage.

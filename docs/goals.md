# Goals

Goals give the agent a persistent, file-backed objective with visible progress and an independent completion review. A goal survives across sessions: the objective, task plan, status, and activity ledger stay on disk under `.vtx/goals/`, so you can resume the same work with a fresh context.

## Goal styles

**Regular** goals describe an outcome; the agent decides how to reach it. Good for research, implementation, debugging, documentation.

**Sisyphus** goals describe work that must happen in a specific order (migrations, staged refactors, release procedures). The agent follows the listed sequence one step at a time and preserves dependencies between steps.

## Creating a goal

```text
/goal add structured logging to the authentication module
```

`/goal [seed]` and `/sisyphus [seed]` start a guided draft: the agent asks focused questions (via `ask_user`), investigates the workspace, proposes an objective plus task plan, and creates the goal only after you confirm.

Use `/goal-direct <objective>` or `/sisyphus-direct <objective>` when the objective is already complete — this creates and focuses the goal immediately without drafting.

The model can also create goals through the single [`goal` tool](tools.md#goal) after an explicit request.

## Managing goals

| Command | Does |
| --- | --- |
| `/goal-list` | List open goals (id, mode, status, progress, path) |
| `/goal-status` | Render the full dashboard into the chat log (`verbose`, `health` variants) |
| `/goal-focus` | Pick which open goal this session focuses on |
| `/goal-unfocus` | Clear the session focus; the goal stays open |
| `/goal-tweak <change>` | Guided revision of the focused objective / plan |
| `/goal-pause` | Pause the focused goal |
| `/goal-resume` | Resume a paused or blocked goal |
| `/goal-clear` | Archive the focused goal (asks for confirmation) |
| `/goal-cancel` | Discard an unconfirmed guided draft |
| `/goal-settings` | Toggle goal behaviour settings |

A project can hold several open goals; each session focuses on at most one. Focus is session-scoped — `/goal-unfocus` detaches this session without touching the shared goal.

## Status widget

While a goal is focused, a compact beacon renders above the editor:

```text
╭─ vtx-goal ─ Add CSV export to reports ──────────────╮
│ goal: running [12m47s 18.2K] (+2 open)              │
├─ Tasks · ✓3 done · 2 open ──── [█████░░░] ──────────┤
│ ✓ t1  Review reports page and data source           │
│ ▸ t3  Add the download button                       │
│ · t4  Add documentation                             │
│ Current  t3 · Add the download button               │
│ Verify   Run npm test with zero failures.           │
│ File     .vtx/goals/active_goal_...                 │
╰─ Esc: pause goal   ctrl+shift+g: expand tasks ──────╯
```

Press `Ctrl+Shift+G` to post the expanded dashboard to the chat log: progress bar, full task tree with subtasks, the current task's contract and evidence, the goal-level verification contract, and recent activity from the durable ledger. `/goal-status` renders the same view.

Task markers: `✓` complete, `▸` current, `~` skipped, `·` pending.

## Auto-continue

While a focused goal is active, vtx keeps working instead of returning control after every turn: when the agent stops short of the objective, a small checkpoint turn is scheduled that points it at the next step. Pressing `Esc` pauses the goal, so auto-continue never runs away from you. `/goal-resume` restarts it.

Disable with `/goal-settings → autoContinue`.

## Completion review

Finishing is explicit: the agent calls `goal(action="update", status="complete")`, which starts an independent auditor sub-agent. The auditor receives the objective, task evidence, and verification contracts, then inspects the real workspace (read-only tools) before ending with `<approved/>` or `<disapproved/>`.

Approved goals are archived as complete under `.vtx/goals/archived/`. If changes are required, the goal stays open with the auditor's feedback attached, and it surfaces in every following turn until addressed.

Turn the auditor off with `/goal-settings → auditorEnabled` (completions then archive without independent approval).

## Verification contracts

Goals and individual tasks can carry plain-text completion requirements, e.g. `Run npm test with zero failures.` Task notes starting with `Contract:` become per-task contracts — completing such a task requires recorded evidence, and the auditor checks both levels.

## Storage

```text
.vtx/goals/active_goal_<timestamp>_<id>.md   open goals
.vtx/goals/archived/<same-name>.md            completed / cleared goals
.vtx/goals/ledger.jsonl                       append-only activity ledger
.vtx/goals/settings.json                      toggles (/goal-settings)
```

Goal files are markdown with an embedded JSON metadata block; the `# Goal Prompt` section is user-editable and re-read from disk before every focused action.

# Goals (`/goal`)

Goals let the agent keep working across turns until a separate evaluator judges a completion condition met. This is useful for open-ended tasks ("refactor the auth module and ensure all tests pass") where you don't want to babysit every turn.

## Usage

```
/goal <condition>
```

Example:

```
/goal Make the test suite pass and add a test for the new parser, or stop after 50 turns
```

The objective text may include a budget clause — `or stop after N turns` / `or stop after N minutes` — which overrides the per-goal turn cap. Otherwise the cap falls back to `goal.max_turns` (and ultimately `agent.max_turns`).

## How it works

- A `Goal` holds state only; it does not call tools.
- Between turns, the loop runs a cheap evaluator LLM call (the configured `goal.evaluator_model`, or the active default) that returns a yes/no decision plus a one-line reason.
- If the verdict is "not done", the loop injects the evaluator's "why not" guidance as a user message so the next turn starts with direction.
- Goal state is persisted to the session and restored on `--resume`.

## Lifecycle states

`pursuing`, `paused`, `achieved`, `unmet`, `budget_limited`. The run ends with a budget-limited event when the turn cap is hit.

## Configuration

See `goal:` in [configuration.md](configuration.md):

```yaml
goal:
  enabled: true          # master switch; /goal is rejected when false
  max_turns: 100         # per-goal turn cap
  max_objective_chars: 4000
  evaluator_provider: "" # empty = active default
  evaluator_model: ""
```

## Extension events

Extensions can observe goal transitions via `goal_start`, `goal_end`, `goal_paused`, `goal_resumed` (see [extensions.md](extensions.md)).

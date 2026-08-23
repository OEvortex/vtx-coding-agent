# E2E Test Coverage Review

Status of the tmux E2E harness and where the gaps are.

## Current harness

The tmux E2E suite lives in the repo skill `.agents/skills/vtx-tmux-test/`:

- `run-e2e-tests.sh` — launches Vtx in a detached tmux session, drives input with `tmux send-keys`, captures panes to `/tmp/vtx-test-*.txt`.
- `setup-test-project.sh` — builds a deterministic throwaway project under `/tmp/vtx-test-project`.
- `SKILL.md` — philosophy and usage.

Design properties: isolated `HOME` (`/tmp/vtx-e2e-home`) so runs never touch real config; output-based evaluation (a reviewer reads captured panes, no brittle assertions in shell); filesystem verification of tool effects.

```bash
bash .agents/skills/vtx-tmux-test/run-e2e-tests.sh
KEEP_E2E_HOME=1 bash .agents/skills/vtx-tmux-test/run-e2e-tests.sh   # debug run
VTX_CMD='uv run vtx --model gpt-5.5' bash ...                        # override launch
```

## What unit tests already cover well

The pytest suite under `tests/` is strong on internals: tools (`tests/tools/`), permissions, compaction, session tree/persistence/resume, config migrations, extensions and hooks, headless rendering, provider resolution, SDK (`tests/sdk/`), and a growing TUI slice (`tests/ui/`, `tests/test_switch_command.py`, launch warnings, update notices).

## Where E2E is thin

These paths only get real coverage when a human drives the TUI, because they involve live streaming + keyboard focus:

1. **Approval flow under load** — approve/deny a mutation *while* a stream is mid-flight; verify the tool result surfaces as denied and the loop continues.
2. **Steering** — `alt+enter` during a running turn; confirm the steer lands on the next turn without killing the run.
3. **Background sub-agents** — dispatch `task background:true`, keep chatting, confirm the completion notice arrives on a later turn.
4. **Session tree round-trip** — branch with `/tree`, page branches with left/right, resume from disk afterwards.
5. **Handoff links** — `/handoff` then click back/forward links across sessions.
6. **Ask-user picker** — multi-select toggling, free-text "Other" answer.
7. **Model/provider hot-switch** — `/model` mid-session; thinking-level cycling reflected in the info bar.
8. **Paste handling** — large multi-line paste becomes a `[paste #N]` marker; expansion works.

## Recommendation

Add cases 1–3 first — they exercise the concurrency machinery (`_TurnRunner`, approval futures, `BackgroundTaskManager`) that unit tests stub out, and they are exactly the flows regressions hurt most in daily use.

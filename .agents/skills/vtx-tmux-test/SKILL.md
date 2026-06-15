---
name: vtx-tmux-test
description: "E2E testing of vtx using tmux sessions; IMPORTANT: only trigger this skill when user asks for e2e testing of vtx"
---

# Vtx Tmux E2E Testing

End-to-end testing of vtx using tmux sessions to programmatically control the TUI application.

## Why Tmux?

Vtx is a TUI (Textual-based) app. Running tests programmatically is hard. Tmux provides:

- `tmux new-session` - isolate test environment
- `tmux send-keys` - send keyboard input
- `tmux capture-pane` - capture output
- `tmux has-session` - check if vtx is running

## Test Design Philosophy

- **Deterministic**: Shell scripts create reproducible test environments
- **Isolated config**: Tests run with `HOME=/tmp/vtx-e2e-home` so runtime settings do not mutate the real user config; auth JSON files are copied into the temp HOME when present so provider startup still works
- **Separation of concerns**: Shell script runs steps and captures output; vtx/the reviewer evaluates results
- **Output-based evaluation**: Test success/failure determined by reading output files, not shell script heuristics
- **UI-focused**: Test triggers (`@`, `/`, runtime pickers, keybindings) by checking UI elements appear
- **Filesystem verification**: Tool execution is verified through files under `/tmp/vtx-test-project`

## Quick Start

```bash
# Run all e2e tests from the repo root
bash .agents/skills/vtx-tmux-test/run-e2e-tests.sh

# Optional: keep the temporary HOME for debugging
KEEP_E2E_HOME=1 bash .agents/skills/vtx-tmux-test/run-e2e-tests.sh

# Optional: override launch command/provider/model
VTX_CMD='uv run vtx --model gpt-5.5' \
  bash .agents/skills/vtx-tmux-test/run-e2e-tests.sh
```

After running, read `/tmp/vtx-test-*.txt` and evaluate the captured pane/config/filesystem outputs.

## Test Scripts

### Setup Script: `setup-test-project.sh`

Creates a deterministic test project structure at `/tmp/vtx-test-project/`.

```bash
bash .agents/skills/vtx-tmux-test/setup-test-project.sh
```

### Main Test Script: `run-e2e-tests.sh`

Runs comprehensive e2e tests including UI triggers, runtime controls, tab completion, and tool execution.

```bash
bash .agents/skills/vtx-tmux-test/run-e2e-tests.sh
```

## Test Categories

### UI Trigger Tests (LLM-independent)

- **/ commands**: Type `/`, verify slash command list appears with core and newer commands
- **@ file search**: Type `@pyproject`, verify file picker appears with `pyproject.toml`
- **/model command**: Type `/model`, verify model selector appears, then dismiss
- **/new command**: Type `/new`, verify new conversation is started
- **/resume command**: Type `/resume`, verify session list appears, then dismiss
- **/session command**: Type `/session`, verify session info/statistics displayed

### Runtime Mode Tests (LLM-independent)

- **/permissions picker**: Shows `prompt` and `auto`, with current mode checked
- **/permissions auto/prompt**: Info bar updates (`✓✓ auto` / `⏸ prompt`) and temp config persists `permissions.mode`
- **Shift+Tab**: Cycles permission mode and persists it in temp config
- **/thinking picker**: Shows `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, with current level checked
- **/thinking minimal**: Info bar model/thinking area updates to `minimal`
- **Ctrl+Shift+T**: Cycles thinking level in the info bar
- **/notifications picker**: Shows `on` and `off`, with current mode checked
- **/notifications on/off**: Status says saved and temp config persists `notifications.enabled`
- **Info bar row2 regression**: Permission mode remains row2-left while model/provider/thinking remains row2-right after runtime changes

### Tab Path Completion Tests (LLM-independent)

- **Unique match**: Type `pypr` + Tab, verify completes to `pyproject.toml`
- **Multiple alternatives**: Type `src/vtx/ui/s` + Tab, verify floating list shows `selection_mode.py`, `session_ui.py`, `styles.py`
- **Nested unique file**: Type `src/vtx/ui/widg` + Tab, verify completes to `src/vtx/ui/widgets.py`
- **Select from list**: Type `src/vtx/ui/s` + Tab + Enter, verify first completion is applied to input

### Tool Execution Tests (Filesystem verification)

- **Write tool**: Creates `/tmp/vtx-test-project/test1.txt`, verified by file existence
- **Edit tool**: Modifies `test1.txt`, verified by content changing from `hello` to `world`
- **List files**: Shows directory contents in captured pane
- **Calculation**: Computes `3+3`, verified in LLM output where practical

## Configuration

Edit or override environment variables for `run-e2e-tests.sh`:

```bash
WAIT_TIME=30                    # Time for LLM to complete all tool tasks
COMMAND_WAIT_TIME=3             # Time for UI commands to settle
SESSION_NAME="vtx-test"         # Tmux session name
TEST_DIR="/tmp/vtx-test-project" # Test project directory for tool execution
TEST_HOME="/tmp/vtx-e2e-home"    # Isolated HOME/config/session directory
VTX_DIR="$PWD"                  # Vtx repo directory for tab completion tests
VTX_CMD="uv run vtx --model gpt-5.5"
KEEP_E2E_HOME=0                 # Set to 1 to preserve temp HOME after run
```

## Output Files

The main script writes captured outputs to `/tmp/vtx-test-*.txt`:

- `/tmp/vtx-test-1-commands.txt` - `/` slash command list
- `/tmp/vtx-test-2-at-trigger.txt` - `@pyproject` file picker
- `/tmp/vtx-test-3-model.txt` - `/model` selector
- `/tmp/vtx-test-4-new.txt` - `/new` result
- `/tmp/vtx-test-5-permissions-picker.txt` - `/permissions` picker
- `/tmp/vtx-test-6-permissions-auto.txt` and `...-config.txt` - `/permissions auto`
- `/tmp/vtx-test-7-permissions-prompt.txt` and `...-config.txt` - `/permissions prompt`
- `/tmp/vtx-test-8-permissions-shift-tab.txt` and `...-config.txt` - Shift+Tab mode cycling
- `/tmp/vtx-test-9-thinking-picker.txt` - `/thinking` picker
- `/tmp/vtx-test-10-thinking-minimal.txt` - `/thinking minimal`
- `/tmp/vtx-test-11-thinking-cycle.txt` - Ctrl+Shift+T thinking cycle
- `/tmp/vtx-test-12-notifications-picker.txt` - `/notifications` picker
- `/tmp/vtx-test-13-notifications-on.txt` and `...-config.txt` - `/notifications on`
- `/tmp/vtx-test-14-notifications-off.txt` and `...-config.txt` - `/notifications off`
- `/tmp/vtx-test-15-tab-unique.txt` - Tab completion unique match
- `/tmp/vtx-test-16-tab-multiple.txt` - Tab completion alternatives
- `/tmp/vtx-test-17-tab-nested-unique.txt` - Nested unique file completion
- `/tmp/vtx-test-18-tab-select.txt` - Tab completion selection
- `/tmp/vtx-test-19-tools.txt` - Tool execution turn
- `/tmp/vtx-test-20-session.txt` - `/session` stats
- `/tmp/vtx-test-21-resume.txt` - `/resume` session list
- `/tmp/vtx-test-files.txt` - Test project file listing
- `/tmp/vtx-test-test1-content.txt` - Final `test1.txt` content or `FILE_NOT_FOUND`
- `/tmp/vtx-test-session-files.txt` - Session JSONL paths under temp HOME
- `/tmp/vtx-test-final-config.txt` - Final temp config

## Key Tmux Gotchas

- **Use `Escape` not `Esc`**: tmux recognizes `Escape`. `Esc` sends literal characters.
- **Always clear input between tests**: Use `Escape` to dismiss completions, then `C-u` to clear text.
- **Completion selectors block input**: Selectors intercept Enter/Escape; dismiss them before the next test.
- **Shift+Tab**: The script sends CSI Z via `Escape '[' 'Z'` rather than relying on a tmux key name.
- **Ctrl+Shift+T**: The script sends CSI-u `Escape '[84;6u'` because `C-S-t` often collapses to Ctrl+T.

## Test Evaluation (by Vtx/reviewer)

After running the test script, evaluate results by reading the output files.

### What to Check

**UI Trigger Tests:**

- `/` test: Slash command list includes `github`, `themes`, `permissions`, `thinking`, `notifications`, `init`, `compact`, `handoff`, `export`, `copy`, `login`, `logout`
- `@` test: File picker appears and shows `pyproject.toml`
- `/model` test: Model selector appears with model list/current markers
- `/new` test: `Started new conversation` appears
- `/resume` test: Session list appears with prior sessions
- `/session` test: Session info/statistics displayed

**Runtime Mode Tests:**

- `/permissions` picker shows `prompt` and `auto`, current item checked
- `/permissions auto` shows `✓✓ auto`, saved status, and config has `mode = "auto"`
- `/permissions prompt` shows `⏸ prompt`, saved status, and config has `mode = "prompt"`
- Shift+Tab toggles back to `auto` and config has `mode = "auto"`
- `/thinking` picker shows `none`, `minimal`, `low`, `medium`, `high`, `xhigh`
- `/thinking minimal` shows `Thinking level changed to minimal` and info bar row2-right includes `minimal`
- Ctrl+Shift+T changes info bar thinking level from `minimal` to the next level
- `/notifications` picker shows `on` and `off`, current item checked
- `/notifications on/off` status says saved and config flips `enabled = true/false`
- Permission mode remains in row2-left and model/provider/thinking remains row2-right

**Tab Path Completion Tests:**

- `pypr` + Tab shows `pyproject.toml`
- `src/vtx/ui/s` + Tab shows `selection_mode.py`, `session_ui.py`, `styles.py`
- `src/vtx/ui/widg` + Tab shows `src/vtx/ui/widgets.py`
- `src/vtx/ui/s` + Tab + Enter applies a selected completion

**Tool Execution Tests:**

- `/tmp/vtx-test-project/test1.txt` exists
- `/tmp/vtx-test-test1-content.txt` contains `world`
- `/tmp/vtx-test-files.txt` lists `test1.txt`
- `/tmp/vtx-test-19-tools.txt` shows relevant tool blocks/results

### Tabular Report

Provide a summary showing:

- Test name
- Status (PASS/FAIL)
- Description/failure reason
- Overall success rate

### IMPORTANT: Always offer the view command

After presenting the report, ALWAYS give the user this shell command so they can inspect raw captured outputs:

```bash
for f in /tmp/vtx-test-*.txt; do printf "\n\033[1;36m▶▶▶ %s\033[0m\n" "$f"; awk 'NF{found=1} found{lines[++n]=$0} END{while(n>0 && lines[n]=="") n--; for(i=1;i<=n;i++) print lines[i]}' "$f"; done
```

## Cleanup

```bash
# Test script auto-cleans tmux session and temp HOME unless KEEP_E2E_HOME=1.
# Output files remain for evaluation (/tmp/vtx-test-*.txt).
# Manual cleanup if needed:
tmux kill-session -t vtx-test 2>/dev/null
rm -rf /tmp/vtx-test-project /tmp/vtx-e2e-home
rm -f /tmp/vtx-test-*.txt
```

## Tmux Commands Reference

```bash
# Session management
tmux new-session -d -s <name> -c <dir> '<command>'
tmux kill-session -t <name>
tmux has-session -t <name>

# Input — IMPORTANT: use full key names (Escape, Enter, not Esc)
tmux send-keys -t <name> "text"
tmux send-keys -t <name> Enter
tmux send-keys -t <name> Escape
tmux send-keys -t <name> Tab
tmux send-keys -t <name> C-c
tmux send-keys -t <name> C-u

# Output
tmux capture-pane -t <name> -p
tmux capture-pane -t <name> -p > file.txt
```

## Tips

- Tests are deterministic: project/config structure is recreated each run.
- Runtime mode tests are LLM-independent and should be checked first.
- Tab completion tests run from the vtx repo to use known paths.
- Tool tests verify filesystem state; avoid relying solely on LLM prose.
- Use `KEEP_E2E_HOME=1` to inspect temp config/session files after failures.
- Run tool execution before `/resume` so there is a session with messages in the list.

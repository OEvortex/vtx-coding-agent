#!/bin/bash

# Comprehensive e2e test script for ctx
# Tests UI triggers (@, / commands, runtime mode controls, Tab completion) and tool execution.
# This script runs steps and captures output — evaluation is done by ctx reading the output files.

set -u

# Configuration
WAIT_TIME=${WAIT_TIME:-30}                    # Time for LLM to complete tool tasks
COMMAND_WAIT_TIME=${COMMAND_WAIT_TIME:-3}     # Time for UI commands to settle
SESSION_NAME=${SESSION_NAME:-"ctx-test"}
TEST_DIR=${TEST_DIR:-"/tmp/ctx-test-project"}
TEST_HOME=${TEST_HOME:-"/tmp/ctx-e2e-home"}
CTX_DIR=${CTX_DIR:-"$PWD"}                   # use caller's current working directory
CTX_CMD=${CTX_CMD:-"uv run ctx --model gpt-5.5"}
KEEP_E2E_HOME=${KEEP_E2E_HOME:-0}

# Helper functions
cleanup() {
    tmux kill-session -t "$SESSION_NAME" 2>/dev/null || true
    if [ "$KEEP_E2E_HOME" != "1" ]; then
        rm -rf "$TEST_HOME"
    fi
}

capture() {
    tmux capture-pane -t "$SESSION_NAME" -p > "$1"
}

capture_config() {
    if [ -f "$TEST_HOME/.ctx/config.yml" ]; then
        cp "$TEST_HOME/.ctx/config.yml" "$1"
    else
        echo "CONFIG_NOT_FOUND" > "$1"
    fi
}

# Dismiss any open completion/selector and clear the input line.
# Uses Escape (NOT "Esc" which tmux would send as literal text).
clear_input() {
    tmux send-keys -t "$SESSION_NAME" Escape
    sleep 0.5
    tmux send-keys -t "$SESSION_NAME" Escape
    sleep 0.5
    tmux send-keys -t "$SESSION_NAME" C-u
    sleep 0.5
}

run_command() {
    local command="$1"
    tmux send-keys -t "$SESSION_NAME" "$command"
    sleep 1
    tmux send-keys -t "$SESSION_NAME" Enter
    sleep "$COMMAND_WAIT_TIME"
}

send_shift_tab() {
    # tmux doesn't reliably expose a portable S-Tab key name, so send the terminal
    # escape sequence directly: CSI Z.
    tmux send-keys -t "$SESSION_NAME" Escape '[' 'Z'
}

send_ctrl_shift_t() {
    # CSI-u encoding for Ctrl+Shift+T. This is more reliable in tmux than C-S-t,
    # which collapses to Ctrl+T in many terminals.
    tmux send-keys -t "$SESSION_NAME" Escape '[84;6u'
}

# Cleanup on exit
trap cleanup EXIT

# === Setup ===
echo "Setting up isolated e2e environment..."
cleanup
rm -rf "$TEST_DIR" "$TEST_HOME"
mkdir -p "$TEST_DIR" "$TEST_HOME/.ctx"
if [ -f "$HOME/.ctx/openai_auth.json" ]; then
    cp "$HOME/.ctx/openai_auth.json" "$TEST_HOME/.ctx/openai_auth.json"
fi
if [ -f "$HOME/.ctx/copilot_auth.json" ]; then
    cp "$HOME/.ctx/copilot_auth.json" "$TEST_HOME/.ctx/copilot_auth.json"
fi
cd "$TEST_DIR" || exit 1
printf '# Test Project\n' > README.md
printf '{"name": "test"}\n' > config.json

# Let ctx create its default config in the temp HOME. Runtime tests mutate only
# this isolated file, never the real user config.

# Clean up old test output files
rm -f /tmp/ctx-test-*.txt

# === Start ctx (from ctx repo for tab completion tests) ===
echo "Starting ctx in tmux from ctx repo with HOME=$TEST_HOME..."
tmux new-session -d -s "$SESSION_NAME" -c "$CTX_DIR" "HOME=$TEST_HOME OPENAI_API_KEY=\"${OPENAI_API_KEY:-}\" ZAI_API_KEY=\"${ZAI_API_KEY:-}\" ANTHROPIC_API_KEY=\"${ANTHROPIC_API_KEY:-}\" AZURE_AI_FOUNDRY_API_KEY=\"${AZURE_AI_FOUNDRY_API_KEY:-}\" AZURE_AI_FOUNDRY_BASE_URL=\"${AZURE_AI_FOUNDRY_BASE_URL:-}\" $CTX_CMD"
sleep 5  # Give ctx time to start and render UI

# =============================================================================
# Test 1: / slash commands trigger
# Verify: typing / shows the slash command list, including newer runtime commands
# =============================================================================
echo "Test 1: / slash commands trigger..."
tmux send-keys -t "$SESSION_NAME" '/'
sleep 2
capture /tmp/ctx-test-1-commands.txt
clear_input

# =============================================================================
# Test 2: @ file search trigger
# Verify: typing @pyproject shows file picker with pyproject.toml (from ctx repo)
# =============================================================================
echo "Test 2: @ file search trigger..."
tmux send-keys -t "$SESSION_NAME" '@pyproject'
sleep 2
capture /tmp/ctx-test-2-at-trigger.txt
clear_input

# =============================================================================
# Test 3: /model command
# Verify: /model shows model selector list, then dismiss without selecting
# =============================================================================
echo "Test 3: /model command..."
tmux send-keys -t "$SESSION_NAME" '/model'
sleep 2
tmux send-keys -t "$SESSION_NAME" Enter
sleep "$COMMAND_WAIT_TIME"
capture /tmp/ctx-test-3-model.txt
clear_input

# =============================================================================
# Test 4: /new command
# Verify: /new starts a new conversation ("Started new conversation" message)
# =============================================================================
echo "Test 4: /new command..."
run_command '/new'
capture /tmp/ctx-test-4-new.txt

# =============================================================================
# P0 runtime mode controls and info bar
# =============================================================================
echo "Test 5: /permissions picker..."
tmux send-keys -t "$SESSION_NAME" '/permissions'
sleep 2
tmux send-keys -t "$SESSION_NAME" Enter
sleep "$COMMAND_WAIT_TIME"
capture /tmp/ctx-test-5-permissions-picker.txt
clear_input

echo "Test 6: /permissions auto..."
run_command '/permissions auto'
capture /tmp/ctx-test-6-permissions-auto.txt
capture_config /tmp/ctx-test-6-permissions-auto-config.txt

echo "Test 7: /permissions prompt..."
run_command '/permissions prompt'
capture /tmp/ctx-test-7-permissions-prompt.txt
capture_config /tmp/ctx-test-7-permissions-prompt-config.txt

echo "Test 8: Shift+Tab permission cycling..."
send_shift_tab
sleep "$COMMAND_WAIT_TIME"
capture /tmp/ctx-test-8-permissions-shift-tab.txt
capture_config /tmp/ctx-test-8-permissions-shift-tab-config.txt

echo "Test 9: /thinking picker..."
tmux send-keys -t "$SESSION_NAME" '/thinking'
sleep 2
tmux send-keys -t "$SESSION_NAME" Enter
sleep "$COMMAND_WAIT_TIME"
capture /tmp/ctx-test-9-thinking-picker.txt
clear_input

echo "Test 10: /thinking minimal..."
run_command '/thinking minimal'
capture /tmp/ctx-test-10-thinking-minimal.txt

echo "Test 11: Ctrl+Shift+T thinking cycling..."
send_ctrl_shift_t
sleep "$COMMAND_WAIT_TIME"
capture /tmp/ctx-test-11-thinking-cycle.txt

echo "Test 12: /notifications picker..."
tmux send-keys -t "$SESSION_NAME" '/notifications'
sleep 2
tmux send-keys -t "$SESSION_NAME" Enter
sleep "$COMMAND_WAIT_TIME"
capture /tmp/ctx-test-12-notifications-picker.txt
clear_input

echo "Test 13: /notifications on..."
run_command '/notifications on'
capture /tmp/ctx-test-13-notifications-on.txt
capture_config /tmp/ctx-test-13-notifications-on-config.txt

echo "Test 14: /notifications off..."
run_command '/notifications off'
capture /tmp/ctx-test-14-notifications-off.txt
capture_config /tmp/ctx-test-14-notifications-off-config.txt

# =============================================================================
# Test 15: Tab completion - unique match
# Verify: typing "pypr" then Tab completes to "pyproject.toml"
# =============================================================================
echo "Test 15: Tab completion - unique match..."
tmux send-keys -t "$SESSION_NAME" 'pypr'
sleep 1
tmux send-keys -t "$SESSION_NAME" Tab
sleep 2
capture /tmp/ctx-test-15-tab-unique.txt
clear_input

# =============================================================================
# Test 16: Tab completion - multiple alternatives (floating list)
# Verify: typing "src/ctx/ui/s" then Tab shows a list including:
# selection_mode.py, session_ui.py, styles.py
# =============================================================================
echo "Test 16: Tab completion - multiple alternatives..."
tmux send-keys -t "$SESSION_NAME" 'src/ctx/ui/s'
sleep 1
tmux send-keys -t "$SESSION_NAME" Tab
sleep 2
capture /tmp/ctx-test-16-tab-multiple.txt
clear_input

# =============================================================================
# Test 17: Tab completion - nested unique file
# Verify: typing "src/ctx/ui/widg" then Tab completes to "src/ctx/ui/widgets.py"
# =============================================================================
echo "Test 17: Tab completion - nested unique file..."
tmux send-keys -t "$SESSION_NAME" 'src/ctx/ui/widg'
sleep 1
tmux send-keys -t "$SESSION_NAME" Tab
sleep 2
capture /tmp/ctx-test-17-tab-nested-unique.txt
clear_input

# =============================================================================
# Test 18: Tab completion - select from list
# Verify: typing "src/ctx/ui/s" Tab shows list, then Enter applies first completion
# =============================================================================
echo "Test 18: Tab completion - select from list..."
tmux send-keys -t "$SESSION_NAME" 'src/ctx/ui/s'
sleep 1
tmux send-keys -t "$SESSION_NAME" Tab
sleep 2
tmux send-keys -t "$SESSION_NAME" Enter
sleep 1
capture /tmp/ctx-test-18-tab-select.txt
clear_input

# =============================================================================
# Test 19: Tool execution (multiple tool calls)
# Verify: creates test1.txt, edits it, lists files, calculates 3+3
# Running this BEFORE /resume so there's a session with messages to resume.
# Permission mode is auto from Shift+Tab above, so approval prompts should not block.
# =============================================================================
echo "Test 19: Tool execution..."
run_command '/new'
tmux send-keys -t "$SESSION_NAME" "Create $TEST_DIR/test1.txt containing \"hello\", then edit $TEST_DIR/test1.txt to change \"hello\" to \"world\", list files in $TEST_DIR, and calculate 3+3. Use parallel tool calls, be quick."
sleep 1
tmux send-keys -t "$SESSION_NAME" Enter
sleep "$WAIT_TIME"
capture /tmp/ctx-test-19-tools.txt

# =============================================================================
# Test 20: /session command
# Verify: shows session info (messages, tokens, file path)
# =============================================================================
echo "Test 20: /session command..."
run_command '/session'
capture /tmp/ctx-test-20-session.txt

# =============================================================================
# Test 21: /resume command
# Verify: shows list of sessions (at least one from tool execution above)
# =============================================================================
echo "Test 21: /resume command..."
tmux send-keys -t "$SESSION_NAME" '/resume'
sleep 2
tmux send-keys -t "$SESSION_NAME" Enter
sleep "$COMMAND_WAIT_TIME"
capture /tmp/ctx-test-21-resume.txt
clear_input

# =============================================================================
# Capture file system/session/config state for verification
# =============================================================================
echo "Capturing file system and persisted state..."
ls -la "$TEST_DIR" > /tmp/ctx-test-files.txt 2>/dev/null
find "$TEST_HOME/.ctx/sessions" -type f -name '*.jsonl' -print > /tmp/ctx-test-session-files.txt 2>/dev/null || true
capture_config /tmp/ctx-test-final-config.txt

# Retry a few times in case the LLM is still finishing file writes
for _ in 1 2 3; do
    if [ -f "$TEST_DIR/test1.txt" ]; then
        cat "$TEST_DIR/test1.txt" > /tmp/ctx-test-test1-content.txt
        break
    fi
    sleep 3
done
# Final check
if [ ! -f "$TEST_DIR/test1.txt" ]; then
    echo "FILE_NOT_FOUND" > /tmp/ctx-test-test1-content.txt
    ls -la "$TEST_DIR" > /tmp/ctx-test-files.txt 2>/dev/null
fi

printf '\n%s\n' "${SEP:-==============================}"
echo "All tests complete"
echo "Output files saved to /tmp/ctx-test-*.txt"
echo "Temp HOME: $TEST_HOME (KEEP_E2E_HOME=1 to keep after run)"
printf '%s\n' "${SEP:-==============================}"

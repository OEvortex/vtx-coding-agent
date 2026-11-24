"""Visualize how vx renders each block type in compact vs expanded view."""

from vx.tui import blocks, ansi

WIDTH = 80
SEPARATOR = f"{ansi.dim()}{'─' * WIDTH}{ansi.RESET}"


def show(label: str, block: blocks.Block):
    print(f"\n{ansi.BOLD}{label}{ansi.RESET}")
    print(SEPARATOR)
    for row in block.rows(WIDTH):
        print(row)


# --- 1. Thinking block (streaming) ---
show(
    "1. THINKING BLOCK (streaming — always same format)",
    blocks.Thinking(
        text="I need to find the relevant files for this task. Let me search the codebase for the bash tool implementation and understand how output is rendered."
    ),
)

# --- 2. Assistant text block (streaming) ---
show(
    "2. ASSISTANT TEXT BLOCK (streaming markdown reply)",
    blocks.Assistant(
        text="Here's how the bash tool works:\n\nThe `bash` tool executes shell commands and captures their output. It supports both stdout and stderr streams, and can optionally send the output to the LLM for analysis.\n\n```python\nresult = await run_command('ls -la')\n```\n\nThe output is then displayed in the transcript with proper formatting."
    ),
)

# --- 3. User prompt ---
show("3. USER PROMPT", blocks.UserTurn(text="run `ls -la` and show me the output"))

# --- 4. Tool status row (compact single line) ---
show(
    "4. TOOL STATUS ROW (bash running)",
    blocks.BashToolStatus(name="bash", command="ls -la", status="running"),
)

show(
    "5. TOOL STATUS ROW (bash completed)",
    blocks.BashToolStatus(name="bash", command="ls -la", status="ok"),
)

# --- 6. ShellRun (user !cmd) — COMPACT ---
blocks.set_expanded(False)
long_output = "\n".join([f"file{i}.txt" for i in range(1, 21)])
show(
    "6. BASH OUTPUT — COMPACT VIEW (ctrl+o)",
    blocks.ShellRun(command="ls -la", output=long_output, done=True, success=True),
)

# --- 7. ShellRun — EXPANDED ---
blocks.set_expanded(True)
show(
    "7. BASH OUTPUT — EXPANDED VIEW (ctrl+o)",
    blocks.ShellRun(command="ls -la", output=long_output, done=True, success=True),
)

# --- 8. ShellRun with error ---
blocks.set_expanded(False)
show(
    "8. BASH OUTPUT — ERROR (compact)",
    blocks.ShellRun(
        command="cat missing.txt",
        output="cat: missing.txt: No such file or directory",
        done=True,
        success=False,
    ),
)

# --- 9. ToolOutput (streaming tool output) — COMPACT ---
many_lines = [f"output line {i}" for i in range(1, 16)]
show("9. TOOL OUTPUT — COMPACT VIEW", blocks.ToolOutput(lines=many_lines, hidden=10))

# --- 10. ToolOutput — EXPANDED ---
blocks.set_expanded(True)
show("10. TOOL OUTPUT — EXPANDED VIEW", blocks.ToolOutput(lines=many_lines, hidden=10))

# --- 11. DiffOutput — COMPACT ---
diff_lines = [
    (" ", "1", "import os"),
    (" ", "2", "import sys"),
    ("-", "3", "from old_module import old_func"),
    ("+", "3", "from new_module import new_func"),
    (" ", "4", ""),
    (" ", "5", "def main():"),
    ("-", "6", "    old_func()"),
    ("+", "6", "    new_func()"),
    ("+", "7", "    print('hello')"),
    (" ", "8", ""),
    (" ", "9", "if __name__ == '__main__':"),
    (" ", "10", "    main()"),
    ("+", "11", ""),
    ("+", "12", "def helper():"),
    ("+", "13", "    return 42"),
    ("+", "14", ""),
    ("+", "15", "def another():"),
    ("+", "16", "    pass"),
    ("+", "17", ""),
    ("+", "18", "# end of file"),
    ("+", "19", "print('done')"),
]
blocks.set_expanded(False)
show("11. DIFF OUTPUT — COMPACT VIEW", blocks.DiffOutput(lines=diff_lines))

# --- 12. DiffOutput — EXPANDED ---
blocks.set_expanded(True)
show("12. DIFF OUTPUT — EXPANDED VIEW", blocks.DiffOutput(lines=diff_lines))

# --- 13. ToolResult (code panel) — COMPACT ---
result_lines = [f"line {i}: some code content" for i in range(1, 25)]
blocks.set_expanded(False)
show(
    "13. TOOL RESULT (code file) — COMPACT VIEW",
    blocks.ToolResult(label="src/app.py", lines=result_lines, language="python"),
)

# --- 14. ToolResult — EXPANDED ---
blocks.set_expanded(True)
show(
    "14. TOOL RESULT (code file) — EXPANDED VIEW",
    blocks.ToolResult(label="src/app.py", lines=result_lines, language="python"),
)

# --- 15. Notice block ---
blocks.set_expanded(False)
show(
    "15. NOTICE BLOCK",
    blocks.Notice(topic="Permissions", body="Tool wants to run bash command", tone="warning"),
)

# --- 16. Turn summary ---
show(
    "16. TURN SUMMARY",
    blocks.TurnSummary(recap="Ran 2 commands · 3 reads", seconds=7.5, tokens_in=30, tokens_out=88),
)

# Reset
blocks.set_expanded(False)

print("\n")
print(f"{ansi.BOLD}KEY DIFFERENCES BETWEEN COMPACT AND EXPANDED:{ansi.RESET}")
print(SEPARATOR)
print()
print(f"  {ansi.BOLD}Compact view (default):{ansi.RESET}")
print("    - Bash output:  │ prefix, up to 5 preview lines, then fold hint")
print("    - Tool output:  2-space indent, up to 5 preview lines")
print("    - Diffs:        │ prefix with line numbers, up to 10 lines")
print("    - Code panels:  framed code block, up to 20 lines")
print()
print(f"  {ansi.BOLD}Expanded view (ctrl+o):{ansi.RESET}")
print("    - Bash output:  4-space indent (no │), ALL lines shown")
print("    - Tool output:  4-space indent (no │), ALL lines shown")
print("    - Diffs:        ALL lines shown (no limit)")
print("    - Code panels:  up to 200 lines")
print()
print(f"  {ansi.BOLD}Thinking & Assistant text:{ansi.RESET}")
print("    - Same format in both views (not affected by ctrl+o)")
print()

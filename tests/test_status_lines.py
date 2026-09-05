from __future__ import annotations

from vtx.tui.status_lines import (
    AGENT_STATUS_LINES,
    RECAP_STATUS_LINES,
    TOOL_ERROR_LINES,
    TOOL_STATUS_LINES,
    WITTY_STATUS_LINES,
    pick_agent_status_line,
    pick_recap_status_line,
    pick_tool_error_line,
    pick_tool_status_line,
    pick_witty_line,
)

EXPECTED_TOOLS = [
    "read",
    "edit",
    "write",
    "bash",
    "find",
    "grep",
    "skill",
    "web",
    "web_search",
    "ask_user",
    "task",
    "goal",
    "default",
]

EXPECTED_AGENT_STATES = ["thinking", "reasoning", "compacting", "approval", "general"]


def test_agent_status_lines_have_at_least_10_entries() -> None:
    for state in EXPECTED_AGENT_STATES:
        assert state in AGENT_STATUS_LINES, f"Agent state '{state}' missing in AGENT_STATUS_LINES"
        lines = AGENT_STATUS_LINES[state]
        assert len(lines) >= 10, f"State '{state}' has only {len(lines)} lines, expected >= 10"


def test_tool_status_lines_have_at_least_10_entries() -> None:
    for tool in EXPECTED_TOOLS:
        assert tool in TOOL_STATUS_LINES, f"Tool '{tool}' missing in TOOL_STATUS_LINES"
        lines = TOOL_STATUS_LINES[tool]
        assert len(lines) >= 10, (
            f"Tool '{tool}' has only {len(lines)} status lines, expected >= 10"
        )


def test_tool_error_lines_have_at_least_10_entries() -> None:
    for tool in EXPECTED_TOOLS:
        assert tool in TOOL_ERROR_LINES, f"Tool '{tool}' missing in TOOL_ERROR_LINES"
        lines = TOOL_ERROR_LINES[tool]
        assert len(lines) >= 10, f"Tool '{tool}' has only {len(lines)} error lines, expected >= 10"


def test_pick_tool_status_line() -> None:
    for tool in EXPECTED_TOOLS:
        line = pick_tool_status_line(tool)
        assert line, f"pick_tool_status_line returned empty for {tool}"
        assert line in TOOL_STATUS_LINES[tool]

    # Test unknown tool falls back to default
    unknown = pick_tool_status_line("unknown_custom_tool")
    assert unknown in TOOL_STATUS_LINES["default"]


def test_pick_tool_error_line() -> None:
    for tool in EXPECTED_TOOLS:
        line = pick_tool_error_line(tool)
        assert line, f"pick_tool_error_line returned empty for {tool}"
        assert line in TOOL_ERROR_LINES[tool]

    # Test unknown tool error falls back to default
    unknown = pick_tool_error_line("non_existent_tool")
    assert unknown in TOOL_ERROR_LINES["default"]


def test_pick_agent_status_line() -> None:
    for state in EXPECTED_AGENT_STATES:
        line = pick_agent_status_line(state)
        assert line, f"pick_agent_status_line returned empty for {state}"
        assert line in AGENT_STATUS_LINES[state]


def test_pick_witty_line_dispatching() -> None:
    # Tool status
    t_line = pick_witty_line(tool_name="bash")
    assert t_line in TOOL_STATUS_LINES["bash"]

    # Tool error
    e_line = pick_witty_line(tool_name="bash", is_error=True)
    assert e_line in TOOL_ERROR_LINES["bash"]

    # Agent state
    s_line = pick_witty_line(state="compacting")
    assert s_line in AGENT_STATUS_LINES["compacting"]

    # Default fallback
    gen_line = pick_witty_line()
    assert gen_line in WITTY_STATUS_LINES


def test_witty_line_exclusion() -> None:
    pool = TOOL_STATUS_LINES["read"]
    first = pool[0]
    # If we exclude first, pick_tool_status_line should pick another line
    picked = pick_tool_status_line("read", exclude=first)
    assert picked != first
    assert picked in pool


def test_recap_status_lines_have_at_least_10_entries() -> None:
    assert len(RECAP_STATUS_LINES) >= 10


def test_pick_recap_status_line() -> None:
    line = pick_recap_status_line()
    assert line
    assert line in RECAP_STATUS_LINES


def test_pick_witty_line_recap_state() -> None:
    line = pick_witty_line(state="recap")
    assert line in RECAP_STATUS_LINES

"""Tests for the Task tool UI renderers (vtx.tui.task_ui)."""

from rich.text import Text

from vtx.tui import task_ui


def test_format_tokens_compact() -> None:
    assert task_ui.format_tokens(500) == "500 tokens"
    assert task_ui.format_tokens(33_800) == "33.8k tokens"
    assert task_ui.format_tokens(1_200_000) == "1.2M tokens"


def test_format_tool_breakdown() -> None:
    assert (
        task_ui.format_tool_breakdown({"read": 4, "grep": 3, "bash": 1})
        == "8 tool calls (4 reads, 3 searches, 1 bash)"
    )
    assert task_ui.format_tool_breakdown({"read": 1}) == "1 tool call (1 read)"
    assert task_ui.format_tool_breakdown({}) == "0 tool calls"


def test_extract_summary_line() -> None:
    text = (
        "## Heading\n\n- Identified 3 core dispatch layers"
        " and mapped lifecycle.\n\nMore details..."
    )
    assert (
        task_ui.extract_summary_line(text)
        == "Identified 3 core dispatch layers and mapped lifecycle."
    )


def test_detect_files_referenced() -> None:
    text = "Modified src/vtx/tui/app.py and tests/ui/test_task_ui.py."
    assert task_ui.detect_files_referenced(text) == 2


def test_format_turns_with_and_without_limit() -> None:
    assert task_ui.format_turns(5) == "↻5"
    assert task_ui.format_turns(5, 30) == "↻5≤30"


def test_describe_activity_maps_known_tool() -> None:
    assert task_ui.describe_activity("read", "") == "reading…"
    assert task_ui.describe_activity("bash", "") == "running command…"
    assert task_ui.describe_activity("custom_tool", "") == "custom_tool…"


def test_describe_activity_falls_back_to_text_then_thinking() -> None:
    assert task_ui.describe_activity(None, "hello\nworld") == "hello"
    long = "x" * 80
    rendered = task_ui.describe_activity(None, long)
    assert rendered == "x" * 60 + "…"
    assert task_ui.describe_activity(None, "") == "thinking…"


def test_stats_parts_order() -> None:
    stats = {
        "model": "anthropic/claude-sonnet-4-5",
        "turns": 3,
        "max_turns": 200,
        "tool_uses": 1,
        "tokens": 33_800,
    }
    assert task_ui.stats_parts(stats) == [
        "claude-sonnet-4-5",
        "↻3≤200",
        "1 tool use",
        "33.8k tokens",
    ]


def test_render_receipt_is_static_running_line() -> None:
    """The chat block is a receipt: no spinner, no live counters.

    The pinned Agents panel owns the animated live view.
    """
    text = task_ui.render_receipt(
        {"turns": 2, "tool_uses": 3, "active_tool": "grep", "last_text": ""}
    )
    assert text.plain == f"  {task_ui.GLYPHS['sub_line']}  running…"
    for frame in task_ui.SPINNER:
        assert frame not in text.plain
    assert "2" not in text.plain.replace(task_ui.GLYPHS["sub_line"], "")


def test_render_receipt_reports_queue_position() -> None:
    text = task_ui.render_receipt({"queued": True, "queue_position": 3})
    assert "queued · 3 ahead of the cap" in text.plain

    no_position = task_ui.render_receipt({"queued": True})
    assert no_position.plain.strip().endswith("queued")


def test_render_finished_is_one_line() -> None:
    stats = {
        "turns": 5,
        "tool_counts": {"read": 4, "grep": 3, "bash": 1},
        "tokens": 34_200,
        "final_text": "Identified 3 core dispatch layers and mapped dependency lifecycle.",
    }
    text = task_ui.render_finished(stats, success=True, elapsed_ms=24_100)
    lines = text.plain.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith(f"  {task_ui.GLYPHS['sub_line']} Done")
    assert "5 turns · 8 tool calls (4 reads, 3 searches, 1 bash)" in lines[0]
    assert "34.2k tokens · 24.1s" in lines[0]
    assert "[ctrl+] transcript" in lines[0]


def test_render_finished_error_includes_message() -> None:
    text = task_ui.render_finished({"turns": 1, "error": "boom"}, success=False, elapsed_ms=1_000)
    assert "Error" in text.plain
    assert "boom" in text.plain


def test_render_finished_late_background_error_is_an_error() -> None:
    """A background dispatch already reported success; a later failure in the
    sub-agent must still read as a failure."""
    text = task_ui.render_finished(
        {"turns": 1, "stop_label": "end_turn", "error": "Sub-agent raised: boom"},
        success=True,
        elapsed_ms=1_000,
    )
    assert "Error" in text.plain
    assert "Sub-agent raised: boom" in text.plain


def test_render_finished_stopped_on_interrupt() -> None:
    text = task_ui.render_finished(
        {"turns": 1, "stop_label": "interrupted"}, success=False, elapsed_ms=2_000
    )
    assert "Stopped" in text.plain


def test_render_background_line() -> None:
    text = task_ui.render_background("task_abc")
    assert "Running in background (ID: task_abc)" in text.plain
    assert isinstance(text, Text)


def test_render_finished_expanded() -> None:
    text = task_ui.render_finished(
        {"turns": 1}, success=True, elapsed_ms=1000, result_text="Line 1\nLine 2", expanded=True
    )
    assert "Line 1" in text.plain
    assert "Line 2" in text.plain

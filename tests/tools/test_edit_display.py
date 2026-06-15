from rich.text import Text

from vtx import config
from vtx.tools.edit import format_diff_display


def test_format_diff_display_short_lines_not_truncated() -> None:
    short = " 2 + short line"
    display = format_diff_display(short)
    assert "\u2026" not in display  # … not present
    assert "short line" in display
    assert "▎" not in display


def test_format_diff_display_truncates_long_lines() -> None:
    long_added = " 2 + " + "x" * 300
    long_removed = " 2 - " + "y" * 300

    display = format_diff_display(f"{long_added}\n{long_removed}")
    lines = display.split("\n")

    added_color = config.ui.colors.diff_added
    removed_color = config.ui.colors.diff_removed

    assert len(lines) == 2
    # Lines use an outer background style with nested dim line numbers and colored sign/content.
    assert lines[0].startswith("[on ")
    assert f"[{added_color}]+" in lines[0]
    assert "\u2026" in lines[0]  # … truncation
    assert "▎" not in lines[0]
    assert lines[1].startswith("[on ")
    assert f"[{removed_color}]-" in lines[1]
    assert "\u2026" in lines[1]  # … truncation
    assert "▎" not in lines[1]


def test_format_diff_display_escapes_regex_bracket_literals() -> None:
    line = r' 40   _PASTE_MARKER_RE = re.compile(r"\[paste #(\d+)\]")'

    display = format_diff_display(line)
    text = Text.from_markup(display)

    # Context lines get "  " prefix for alignment with changed rows
    assert text.plain.startswith("   40")
    assert [span.style for span in text.spans] == [config.ui.colors.dim]

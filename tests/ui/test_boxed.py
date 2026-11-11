"""The boxed tool-render chrome: width discipline, badges, and highlighting."""

from __future__ import annotations

from rich.text import Text

from vtx.coding_agent.tools.edit import format_diff_display, generate_diff
from vtx.tui import boxed
from vtx.tui.diff_display import DIFF_BG_PAD_MARKER


def test_top_border_never_overflows_its_width():
    for width in range(20, 140):
        for badge in ("", "PY", "TOML"):
            for status in ("", "✓", "◌", "✗"):
                text = boxed.top_border(
                    "→",
                    "Read",
                    "src/vtx/tui/very/long/path/to/file.py:120-400",
                    width,
                    badge=badge,
                    status=status,
                )
                assert text.cell_len == boxed.box_width(width), (width, badge, status)


def test_bottom_border_never_overflows_its_width():
    for width in range(20, 140):
        for footer in ("", "0.4s · ~12 lines", "Diff · +3 -2"):
            for hint in ("", "ctrl+o to expand"):
                text = boxed.bottom_border(footer, width, hint=hint)
                assert text.cell_len == boxed.box_width(width), (width, footer, hint)


def test_header_carries_language_chip_and_status():
    plain = boxed.top_border("←", "Edit", "src/x.py", 60, badge="PY", status="✓").plain
    assert "PY" in plain
    assert "✓" in plain
    assert plain.startswith("╭─ ← Edit src/x.py")


def test_language_badge_is_derived_from_the_extension():
    assert boxed.language_badge("src/a.py") == "PY"
    assert boxed.language_badge("web/app.tsx") == "TSX"
    assert boxed.language_badge("README.md") == "MD"
    assert boxed.language_badge("notes") == ""
    assert boxed.language_badge(None) == ""


def test_numbered_lines_use_an_editor_gutter():
    preview = boxed.highlighted_numbered_lines("def f() -> int:\n    return 1\n", "x.py")
    lines = preview.plain.split("\n")
    assert lines[0].startswith("1 │ ")
    assert lines[1].startswith("2 │ ")
    assert "return" in lines[1]


def test_diff_rows_are_syntax_highlighted_and_keep_the_marker():
    diff, _, _ = generate_diff("def f():\n    return 1\n", "def f(x: int) -> int:\n    return x\n")
    text = Text.from_markup(format_diff_display(diff))

    highlighted = boxed.highlight_diff(text, "src/x.py")

    assert "+ 1 │" in highlighted.plain
    assert "- 1 │" in highlighted.plain
    assert DIFF_BG_PAD_MARKER in highlighted.plain
    # A plain single-hue diff carries one foreground style per row; the
    # highlighted version paints several syntax tokens.
    assert len(highlighted.spans) > len(text.spans)


def test_diff_without_a_known_lexer_is_left_alone():
    diff, _, _ = generate_diff("a\n", "b\n")
    text = Text.from_markup(format_diff_display(diff))
    assert boxed.highlight_diff(text, "notes.unknownext") is text

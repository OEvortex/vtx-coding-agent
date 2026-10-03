"""Regression tests for markdown rendering: heading hierarchy and tables.

Both are easy to break by accident, and the failure is purely visual, so a
passing suite would not otherwise catch a regression.
"""

from __future__ import annotations

import pytest

from vtx.tui.formatting import MARKDOWN_THEME, CustomMarkdown, format_markdown

HEADINGS = "# One\n## Two\n### Three\n#### Four\n##### Five\n###### Six"

TABLE = "| Flag | Meaning |\n|:-----|:-------|\n| -v | verbose |\n| -q | quiet |"


def test_heading_levels_have_distinct_styles():
    """# and ###### must not both render as plain bold."""
    styles = [MARKDOWN_THEME.styles[f"markdown.h{i}"] for i in range(1, 7)]
    assert len(set(styles)) == 6, "each heading level needs its own style"
    assert all(style.bold for style in styles), "weight must survive on every level"
    # Hierarchy must ramp: h1 is never dimmer than h6.
    assert styles[0].color != styles[-1].color


def test_heading_text_renders_all_levels():
    plain = format_markdown(HEADINGS, width=60).plain
    for level in range(1, 7):
        assert f"{['One', 'Two', 'Three', 'Four', 'Five', 'Six'][level - 1]}" in plain


def test_table_renders_header_and_rows():
    plain = format_markdown(TABLE, width=48).plain
    assert "Flag" in plain and "Meaning" in plain
    assert "-v" in plain and "verbose" in plain
    assert "─" in plain, "header rule missing"


def test_table_has_no_stray_edge_lines():
    """Rich's default table wraps content in padded blank lines; we drop them.

    Two tables in one reply used to render separated by empty rows, which read
    as spurious paragraph breaks.
    """
    lines = format_markdown(TABLE, width=48).plain.split("\n")
    padded_blanks = [ln for ln in lines if ln and not ln.strip()]
    assert padded_blanks == [], f"table padded with blank lines: {lines!r}"


def test_table_rule_is_flush_left():
    lines = [ln for ln in format_markdown(TABLE, width=48).plain.split("\n") if "─" in ln]
    assert lines, "header rule missing"
    assert lines[0].startswith("─"), f"rule should not be indented: {lines[0]!r}"


def test_table_cell_markup_is_preserved():
    plain = format_markdown("| a | b |\n|---|---|\n| **bold** | plain |", width=40).plain
    assert "**" not in plain, "inline emphasis should be consumed inside table cells"
    assert "bold" in plain


def test_custom_markdown_uses_the_vtx_table_renderer():
    from vtx.tui.formatting import VtxTableElement

    assert CustomMarkdown.elements["table_open"] is VtxTableElement


@pytest.mark.parametrize(
    "src,expected",
    [("inline `code` here", "inline code here"), ("**bold** and *em*", "bold and em")],
)
def test_inline_markup_is_consumed(src, expected):
    assert expected in format_markdown(src, width=60).plain


def test_fenced_code_block_is_not_reinterpreted():
    """A pipe table syntax inside a fence must stay literal code."""
    plain = format_markdown("```\n| not | a table |\n```", width=48).plain
    assert "| not | a table |" in plain

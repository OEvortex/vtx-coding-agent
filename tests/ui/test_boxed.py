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


def test_top_border_accepts_pre_styled_text_detail():
    # A bash command arrives as an already-highlighted ``Text``; the header must
    # keep its syntax-coloured spans instead of flattening it back to dim text.
    from vtx.tui.formatting import format_bash_command

    highlighted = format_bash_command("git log --oneline && echo done")
    assert len(highlighted.spans) > 1

    for width in range(20, 140):
        text = boxed.top_border("→", "Bash", highlighted, width)
        assert text.cell_len == boxed.box_width(width), (width,)
    # The command tokens keep their styles after the truncating header pass.
    text = boxed.top_border("→", "Bash", highlighted, 60)
    assert len(text.spans) > 1


def test_bash_tool_header_is_syntax_highlighted():
    from vtx.tui.blocks import ToolBlock

    block = ToolBlock(name="bash", call_msg="$ git log --oneline && echo done")
    header = block._format_header()

    # The command word and the operator ride distinct colours (not one flat dim
    # run) — the highlighter must actually be wired into the header render.
    plain = header.plain
    git_col = plain.index("git")
    op_col = plain.index("&&")

    def style_at(col: int) -> object:
        covering = [s for s in header.spans if s.start <= col < s.end and s.style]
        return covering[-1].style if covering else None

    git_style = style_at(git_col)
    op_style = style_at(op_col)
    assert git_style is not None
    assert op_style is not None
    assert git_style != op_style


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


def test_lexer_is_resolved_from_pygments_not_the_hand_map():
    # Extension-less file names and extensions outside the old hand-kept table
    # must still yield a Pygments lexer; they previously returned None and the
    # diff was left completely unhighlighted ("doesn't work at all").
    assert boxed.lexer_for_path("Dockerfile") == "docker"
    assert boxed.lexer_for_path("Makefile") == "make"
    assert boxed.lexer_for_path("Gemfile") == "ruby"
    assert boxed.lexer_for_path("web/app.tsx") == "tsx"
    assert boxed.lexer_for_path("cmd/main.go") == "go"
    assert boxed.lexer_for_path("data.json") == "json"
    assert boxed.lexer_for_path("services/api.service.ts") in {"typescript", "tsx"}


def test_lexer_for_unknown_extension_is_none():
    assert boxed.lexer_for_path("notes.unknownext") is None
    assert boxed.lexer_for_path(None) is None


def test_extensionless_dockerfile_diff_is_syntax_highlighted():
    old = 'FROM python:3.12\nCMD ["app"]\n'
    new = 'FROM python:3.12-slim\nLABEL maintainer=dev\nCMD ["app"]\n'
    diff, _, _ = generate_diff(old, new)
    text = Text.from_markup(format_diff_display(diff))
    highlighted = boxed.highlight_diff(text, "Dockerfile")
    # Previously the hardcoded LEXERS map returned None for "Dockerfile"
    # (the dict key exists but _extension() yields "" for dot-less names), so
    # this diff came back with no token colours at all.
    assert highlighted is not text
    assert len(highlighted.spans) > len(text.spans)


def test_numbered_lines_use_an_editor_gutter():
    preview = boxed.highlighted_numbered_lines("def f() -> int:\n    return 1\n", "x.py")
    lines = preview.plain.split("\n")
    assert lines[0].startswith("1 │ ")
    assert lines[1].startswith("2 │ ")
    assert "return" in lines[1]


def test_numbered_lines_show_the_full_range_when_max_lines_fits():
    # The read tool hands the TUI the whole range it read for a paged read;
    # when max_lines covers everything, every line renders numbered (no
    # "earlier lines" marker) with the correct start offset.
    content = "\n".join(f"row{i}" for i in range(1, 16))  # 15 lines
    preview = boxed.highlighted_numbered_lines(content, "x.py", max_lines=15, start_line=5)
    rows = preview.plain.split("\n")
    assert len(rows) == 15
    assert rows[0].strip().startswith("5 │ row1")
    assert rows[-1].strip().startswith("19 │ row15")
    assert "earlier lines" not in preview.plain


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


def test_wrap_boxed_soft_wraps_long_lines_within_the_box():
    long_line = (
        "    resp = await self.client.call_endpoint(first, second, third, fourth, "
        "fifth, sixth, seventh, eighth)  # trailing comment that never ends"
    )
    diff, _, _ = generate_diff(
        "def f():\n    return 1\n", f"def f():\n{long_line}\n    return 2\n"
    )
    text = boxed.highlight_diff(Text.from_markup(format_diff_display(diff)), "src/x.py")

    width = 50
    wrapped = boxed.wrap_boxed(text, width)
    rows = wrapped.plain.split("\n")

    # The over-long added line is wrapped across at least two boxed rows, and
    # every row (box chrome included) stays within the chosen width.
    assert len(rows) > 4
    assert all(len(row) <= width for row in rows)
    # A continuation row carries the underlying code but no gutter/line-number
    # header -- an editor-style soft wrap, mirroring a VS Code diff view.
    assert any("call_endpoint(" in row for row in rows)
    assert " 2 │" not in rows[3]


def test_box_width_uses_the_full_widget_width():
    # The box spans the chat column edge to edge: wide terminals get wide
    # boxes (long lines soft-wrap) instead of a hard 100-column cap.
    assert boxed.box_width(200) == 200
    assert boxed.box_width(140) == 140
    assert boxed.box_width(80) == 80
    assert boxed.box_width(10) == boxed.MIN_BOX_WIDTH
    assert boxed.box_width(None) == 80


def test_wrap_boxed_aligns_guttered_continuation_under_the_code_column():
    # A read/diff line that soft-wraps must keep its code aligned under the
    # code column (after the ``282 │ `` / ``+ 2 │ `` gutter), not jump back to
    # the box's left padding like a plain paragraph wrap.
    long_code = (
        "    resp = await self.client.call_endpoint(first, second, third, fourth, fifth, sixth)"
    )
    line = Text()
    line.append("282 ", style="dim")
    line.append("│ ", style="dim")
    line.append(long_code)

    rows = boxed.wrap_boxed(line, 50).plain.split("\n")
    assert len(rows) >= 3
    # First row carries the gutter; continuations carry gutter-width blanks.
    assert rows[0].startswith("│ 282 │ ")
    for row in rows[1:]:
        assert row.startswith("│       "), row  # '│ ' + 6 gutter cells of indent
    # The wrapped code fragment lines up in the same column on every row.
    code_col = rows[0].index("    resp")
    assert rows[1].index("self.client") == code_col


def test_wrap_boxed_preserves_styles_and_backgrounds_across_the_break():
    from rich.style import Style

    long_line = "x" * 120
    diff, _, _ = generate_diff("a\n", long_line + "\n")
    text = boxed.highlight_diff(Text.from_markup(format_diff_display(diff)), "src/x.py")
    # Give the added row a background so we can confirm it survives wrapping.
    text = Text(text.plain, style=Style(bgcolor="red"))

    def has_bg(row: Text) -> bool:
        for span in row.spans:
            style = span.style
            if isinstance(style, str):
                try:
                    style = Style.parse(style)
                except Exception:
                    continue
            if isinstance(style, Style) and style.bgcolor is not None:
                return True
        return False

    wrapped = boxed.wrap_boxed(text, 50)
    rows = wrapped.split("\n")
    assert len(rows) > 2
    # The wrapped continuation rows keep the row's background colour.
    assert all(has_bg(row) for row in rows[1:3])

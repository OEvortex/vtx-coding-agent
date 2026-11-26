"""HTML rendering: tag tree, inline styling and width-aware blocks."""

from vx.tui import ansi
from vx.tui.html import render


def plain_rows(html: str, cols: int = 80) -> list[str]:
    return [ansi.strip_ansi(row) for row in render(html, cols)]


class TestHeadings:
    def test_h1_is_bold_and_underlined(self):
        rows = render("<h1>Title</h1>", 80)
        assert any("Title" in row for row in rows)
        assert any("\x1b[1m" in row for row in rows)
        assert any("\x1b[4m" in row for row in rows)

    def test_h2_gets_a_rule(self):
        rows = plain_rows("<h2>Section</h2>")
        assert any("Section" in row for row in rows)
        assert any("─" in row for row in rows)

    def test_h3_has_no_rule(self):
        rows = plain_rows("<h3>Sub</h3>")
        assert any("Sub" in row for row in rows)
        assert not any("─" in row for row in rows)


class TestInline:
    def test_emphasis_tags(self):
        rows = render("<p><b>bold</b> <i>italic</i> <u>under</u> <del>gone</del></p>", 80)
        joined = "".join(rows)
        for code in ("\x1b[1m", "\x1b[3m", "\x1b[4m", "\x1b[9m"):
            assert code in joined

    def test_inline_code_has_a_background(self):
        rows = render("<p>use <code>foo()</code> now</p>", 80)
        assert any("\x1b[48;2;" in row for row in rows)

    def test_css_color_and_weight(self):
        rows = render('<p><span style="color:#ff6b6b; font-weight:bold">red</span></p>', 80)
        joined = "".join(rows)
        assert "\x1b[38;2;255;107;107m" in joined
        assert "\x1b[1m" in joined

    def test_link_is_an_osc8_hyperlink(self):
        rows = render('<p><a href="https://x.com">docs</a></p>', 80)
        assert any("\x1b]8;;https://x.com\x1b\\" in row for row in rows)

    def test_entities_are_decoded(self):
        rows = plain_rows("<p>a &amp; b &nbsp; c</p>")
        assert any("a & b" in row for row in rows)


class TestBlocks:
    def test_paragraph(self):
        rows = plain_rows("<p>hello paragraph</p>")
        assert any("hello paragraph" in row for row in rows)

    def test_blockquote_has_a_rail(self):
        rows = plain_rows("<blockquote>quoted</blockquote>")
        assert any("│ quoted" in row for row in rows)

    def test_horizontal_rule(self):
        rows = plain_rows("<hr>")
        assert any("─" in row for row in rows)

    def test_break_and_image_alt(self):
        rows = plain_rows("<p>a<br>b<img alt='chart'></p>")
        text = "\n".join(rows)
        assert "a" in text and "b" in text
        assert "[image: chart]" in text

    def test_script_and_style_are_not_rendered(self):
        rows = plain_rows("<p>before</p><script>alert('x')</script><style>p{}</style><p>after</p>")
        text = "\n".join(rows)
        assert "before" in text and "after" in text
        assert "alert" not in text and "p{}" not in text


class TestLists:
    def test_bullets(self):
        rows = plain_rows("<ul><li>one</li><li>two</li></ul>")
        text = "\n".join(rows)
        assert "• one" in text and "• two" in text

    def test_ordered(self):
        rows = plain_rows("<ol><li>first</li><li>second</li></ol>")
        text = "\n".join(rows)
        assert "1. first" in text and "2. second" in text

    def test_nested_list(self):
        rows = plain_rows("<ul><li>parent<ul><li>child</li></ul></li></ul>")
        text = "\n".join(rows)
        assert "parent" in text and "child" in text
        assert "  • child" in text


class TestPreAndTable:
    def test_pre_keeps_indentation_and_label(self):
        rows = plain_rows('<pre class="language-python">def f():\n    return 1</pre>')
        text = "\n".join(rows)
        assert "python" in text
        assert "def f():" in text
        assert "    return 1" in text

    def test_table_renders_cells_and_header_rule(self):
        rows = plain_rows(
            "<table><tr><th>Name</th><th>Value</th></tr><tr><td>cpu</td><td>72</td></tr></table>"
        )
        text = "\n".join(rows)
        assert "Name" in text and "cpu" in text and "72" in text
        assert "┼" in text

    def test_table_shrinks_to_width(self):
        rows = plain_rows(
            "<table><tr><th>long header one</th><th>long header two</th></tr></table>", 30
        )
        assert max((ansi.width(row) for row in rows), default=0) <= 30


class TestWidth:
    def test_paragraph_wraps_at_cols(self):
        rows = render("<p>" + ("word " * 40) + "</p>", 30)
        assert len(rows) > 1
        assert max((ansi.width(row) for row in rows), default=0) <= 30

    def test_empty_html(self):
        assert render("", 80) == []

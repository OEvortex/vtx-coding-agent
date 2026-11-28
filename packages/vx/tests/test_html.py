"""HTML rendering: tag tree, inline styling and width-aware blocks."""

from vx.tui import ansi
from vx.tui.html import CSS, HTML, b, link, mark, raw, render


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

    def test_pre_without_class_uses_code_label(self):
        rows = plain_rows("<pre>plain\n  indented</pre>")
        text = "\n".join(rows)
        assert "─ code " in text
        assert "plain" in text and "  indented" in text

    def test_pre_uses_class_as_label(self):
        rows = plain_rows('<pre class="highlight">body</pre>')
        assert "─ highlight " in "\n".join(rows)

    def test_table_renders_cells_and_header_rule(self):
        rows = plain_rows(
            "<table><tr><th>Name</th><th>Value</th></tr><tr><td>cpu</td><td>72</td></tr></table>"
        )
        text = "\n".join(rows)
        assert "Name" in text and "cpu" in text and "72" in text
        assert "┼" in text

    def test_table_has_markdown_shape(self):
        rows = plain_rows(
            "<table><tr><th>Name</th><th>Value</th></tr><tr><td>cpu</td><td>72</td></tr></table>"
        )
        assert rows[0].startswith("Name")
        assert not rows[0].startswith("│") and not rows[0].endswith("│")
        assert rows[1].count("┼") == 1

    def test_table_right_alignment(self):
        rows = plain_rows(
            "<table><tr><th>Name</th><th>Value</th></tr>"
            "<tr><td>cpu</td><td align='right'>72</td></tr></table>",
            40,
        )
        assert rows[-1].rstrip().endswith("72")

    def test_table_center_alignment_via_css(self):
        rows = plain_rows(
            "<table><tr><th>Long name here</th></tr>"
            "<tr><td style='text-align:center'>cpu</td></tr></table>",
            30,
        )
        assert rows[-1].startswith(" ")
        assert rows[-1].rstrip().endswith("cpu")

    def test_table_shrinks_to_width(self):
        rows = plain_rows(
            "<table><tr><th>long header one</th><th>long header two</th></tr></table>", 30
        )
        assert max((ansi.width(row) for row in rows), default=0) <= 30

    def test_table_shrinks_inside_narrow_block(self):
        rows = plain_rows(
            "<div style='padding:0 10px'><table>"
            "<tr><th>alpha beta gamma delta epsilon</th></tr></table></div>",
            30,
        )
        text = "\n".join(rows)
        assert "alpha" in text
        assert max((ansi.width(row) for row in rows), default=0) <= 30


class TestBoxModel:
    def test_border_draws_side_rails(self):
        rows = plain_rows("<div style='border:1px'>boxed</div>", 40)
        text = "\n".join(rows)
        assert "│ boxed │" in text

    def test_border_uses_border_color(self):
        rows = render("<div style='border:1px; border-color:#ff6b6b'>x</div>", 40)
        assert "\x1b[38;2;255;107;107m" in "".join(rows)

    def test_border_shrinks_content_width(self):
        rows = plain_rows("<div style='border:1px'>" + "word " * 30 + "</div>", 30)
        assert max((ansi.width(row) for row in rows), default=0) <= 30

    def test_padding_and_background_fill_row(self):
        rows = render("<p style='padding:0 4px; background-color:#262a33'>hi</p>", 40)
        joined = "".join(rows)
        assert "\x1b[48;2;38;42;51m" in joined
        assert any("    hi    " in ansi.strip_ansi(row) for row in rows)

    def test_em_padding_uses_terminal_cells(self):
        rows = render(
            "<div style='margin-top:2; padding:1em 2ch; "
            "background-color:#262a33'><p>Card</p></div>",
            50,
        )
        plain = [ansi.strip_ansi(row) for row in rows]
        assert len(rows) == 3
        assert plain[1].startswith("  Card")
        assert all(ansi.width(row) <= 50 for row in rows)

    def test_logical_padding_shorthand_expands(self):
        rows = render("<div style='padding-inline:1em 4ch'>x</div>", 40)
        assert "x" in "\n".join(ansi.strip_ansi(row) for row in rows)
        assert max((ansi.width(row) for row in rows), default=0) <= 40

    def test_pre_respects_block_width(self):
        rows = plain_rows("<pre>" + "x" * 60 + "</pre>", 30)
        code_rows = [row for row in rows if "x" in row]
        assert len(code_rows) > 1
        assert max(ansi.width(row) for row in code_rows) <= 30


class TestWidth:
    def test_paragraph_wraps_at_cols(self):
        rows = render("<p>" + ("word " * 40) + "</p>", 30)
        assert len(rows) > 1
        assert max((ansi.width(row) for row in rows), default=0) <= 30

    def test_empty_html(self):
        assert render("", 80) == []


class TestCSS:
    def test_style_block_is_not_rendered_as_text(self):
        rows = plain_rows("<style>p { color: red }</style><p>ok</p>")
        text = "\n".join(rows)
        assert "ok" in text
        assert "color: red" not in text

    def test_class_selector(self):
        html = "<style>.warn { color: red; font-weight: bold }</style><p class='warn'>watch</p>"
        joined = "".join(render(html, 60))
        assert "\x1b[38;2;255;107;107m" in joined
        assert "\x1b[1m" in joined

    def test_id_beats_class(self):
        html = "<style>.x { color: red } #y { color: green }</style><p id='y' class='x'>v</p>"
        joined = "".join(render(html, 60))
        assert "\x1b[38;2;81;207;102m" in joined
        assert "\x1b[38;2;255;107;107m" not in joined

    def test_inline_style_beats_css(self):
        html = "<style>.x { color: red }</style><p class='x' style='color:blue'>v</p>"
        assert "\x1b[38;2;92;156;255m" in "".join(render(html, 60))

    def test_important_beats_inline_style(self):
        html = "<style>p { color: red !important }</style><p style='color:blue'>v</p>"
        joined = "".join(render(html, 60))
        assert "\x1b[38;2;255;107;107m" in joined
        assert "\x1b[38;2;92;156;255m" not in joined

    def test_css_variables_and_fallback(self):
        html = (
            "<style>:root { --accent: #ff922b }</style>"
            "<p><span style='color:var(--accent)'>a</span>"
            "<span style='color:var(--missing, green)'>b</span></p>"
        )
        joined = "".join(render(html, 60))
        assert "\x1b[38;2;255;146;43m" in joined
        assert "\x1b[38;2;81;207;102m" in joined

    def test_inline_custom_property(self):
        html = "<p style='--c: red; color: var(--c)'>v</p>"
        assert "\x1b[38;2;255;107;107m" in "".join(render(html, 60))

    def test_descendant_selector(self):
        html = (
            "<style>ul li { font-style: italic }</style><ul><li>one</li></ul><ol><li>two</li></ol>"
        )
        joined = "".join(render(html, 60))
        assert "\x1b[3mone" in joined
        assert "\x1b[3mtwo" not in joined

    def test_comma_selector_list(self):
        html = "<style>h1, .x { color: purple }</style><h1>a</h1><p class='x'>b</p>"
        assert "".join(render(html, 60)).count("\x1b[38;2;177;151;252m") >= 2

    def test_text_transform(self):
        text = "\n".join(
            plain_rows(
                "<p style='text-transform:uppercase'>shout</p>"
                "<p style='text-transform:capitalize'>each word</p>"
            )
        )
        assert "SHOUT" in text and "Each Word" in text

    def test_text_align_center(self):
        rows = plain_rows("<div style='text-align:center'>hi</div>", 20)
        assert rows[0].startswith(" ")

    def test_css_argument(self):
        rows = render("<p class='w'>x</p>", 60, css=".w { color: red }")
        assert "\x1b[38;2;255;107;107m" in "".join(rows)

    def test_unknown_property_is_ignored(self):
        rows = render("<style>p { display: flex; color: red }</style><p>ok</p>", 60)
        assert "\x1b[38;2;255;107;107m" in "".join(rows)


class TestBuilders:
    def test_heading_and_paragraph_without_markup(self):
        page = HTML(width=60).h1("Title").p("Hello", bold="world", code="f()")
        rows = page.render()
        text = "\n".join(ansi.strip_ansi(row) for row in rows)
        assert "Title" in text and "Hello world f()" in text
        assert "\x1b[1m" in "".join(rows)

    def test_sections_in_constructor(self):
        page = HTML(width=60, h1="Title", p=["one", "two"], ul=["a", "b"])
        text = "\n".join(ansi.strip_ansi(row) for row in page.render())
        assert "Title" in text and "one" in text and "two" in text
        assert "• a" in text and "• b" in text

    def test_lists_nested_and_table(self):
        page = (
            HTML(width=60)
            .ul(["first", {"text": "parent", "children": ["child"]}])
            .table(["Name", "Lines"], [["html.py", 385], ["ansi.py", 7]], align={"Lines": "right"})
        )
        text = "\n".join(ansi.strip_ansi(row) for row in page.render())
        assert "first" in text and "child" in text
        assert "Name" in text and "html.py" in text and "┼" in text
        assert "│   385" in text

    def test_code_quote_hr_and_inline_helpers(self):
        page = (
            HTML(width=60)
            .code("def f():\n    return 1", lang="python")
            .quote(mark("quoted"))
            .hr()
            .p(markup=f"{b('bold')} and {link('docs', 'https://x.com')}")
        )
        text = "\n".join(ansi.strip_ansi(row) for row in page.render())
        assert "python" in text and "return 1" in text
        assert "│ quoted" in text and "bold and docs" in text
        assert any("─" in row for row in text.splitlines())

    def test_css_attaches_to_html_block(self):
        css = CSS().add("h1", color="red", font_weight="bold")
        page = HTML(width=60).h1("Hi").css(css)
        joined = "".join(page.render())
        assert "\x1b[38;2;255;107;107m" in joined
        assert "\x1b[1m" in joined

    def test_css_variables_and_attach_method(self):
        css = CSS(accent="#ff922b").add("h1", color="var(--accent)")
        page = HTML(width=60).h1("Hi")
        css.attach(page)
        assert "\x1b[38;2;255;146;43m" in "".join(page.render())

    def test_escaping_and_raw_escape_hatch(self):
        safe = HTML(width=60).p("<script>&").text()
        assert "<script>&" in safe
        assert "script" in safe
        loose = HTML(width=60).add(raw("<p>trusted</p>")).text()
        assert "trusted" in loose

    def test_unknown_section_is_rejected(self):
        try:
            HTML(width=60, bogus="x")
        except ValueError as error:
            assert "bogus" in str(error)
        else:
            raise AssertionError("expected ValueError")

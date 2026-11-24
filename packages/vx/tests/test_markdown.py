"""Markdown rendering: structure detection and inline styling."""

import re

from vx.tui import ansi, markdown

_STRIP = re.compile(r"\x1b\[[0-9;]*m")


def plain_rows(text: str, cols: int = 80) -> list[str]:
    return [_STRIP.sub("", r) for r in markdown.render(text, cols)]


class TestHeadings:
    def test_h1_renders_bold_underlined(self):
        rows = markdown.render("# Title", 80)
        assert any("Title" in r for r in rows)
        assert any("\x1b[1m" in r or "\x1b[4m" in r for r in rows)

    def test_h3(self):
        rows = plain_rows("### Sub")
        assert any("Sub" in r for r in rows)


class TestLists:
    def test_bullet(self):
        rows = plain_rows("- item one\n- item two")
        text = "\n".join(rows)
        assert "item one" in text and "item two" in text
        assert "•" in text

    def test_ordered(self):
        rows = plain_rows("1. first\n2. second")
        text = "\n".join(rows)
        assert "first" in text and "1)" in text

    def test_task_checkbox(self):
        rows = plain_rows("- [x] done thing\n- [ ] todo thing")
        text = "\n".join(ansi.strip_ansi(r) for r in rows)
        assert "☑" in text and "☐" in text


class TestFences:
    def test_code_fence_renders_panel(self):
        rows = plain_rows("```python\nprint(1)\n```")
        text = "\n".join(rows)
        assert "python" in text
        assert "print(1)" in text

    def test_unclosed_fence_still_renders(self):
        rows = plain_rows("```\ncode line")
        assert any("code line" in r for r in rows)


class TestQuotesAndRules:
    def test_blockquote(self):
        rows = plain_rows("> quoted text")
        assert any("quoted text" in r for r in rows)
        assert any("│" in r for r in rows)

    def test_horizontal_rule(self):
        rows = plain_rows("---")
        assert any("─" in r for r in rows)


class TestInline:
    def test_inline_code(self):
        rows = markdown.render("use `foo()` here", 80)
        assert any("foo()" in r for r in rows)

    def test_bold(self):
        rows = markdown.render("a **bold** word", 80)
        assert any("\x1b[1m" in r for r in rows)

    def test_link_renders_label(self):
        rows = plain_rows("see [docs](https://x.com)")
        assert any("docs" in r for r in rows)

    def test_image_renders_alt(self):
        rows = plain_rows("![chart](https://x.com/i.png)")
        assert any("chart" in r for r in rows)

    def test_autolink(self):
        rows = markdown.render("visit https://example.com now", 80)
        assert any("https://example.com" in r for r in rows)

    def test_link_is_an_osc8_hyperlink(self):
        rows = markdown.render("see [docs](https://x.com)", 80)
        assert any("\x1b]8;;https://x.com\x1b\\" in r for r in rows)

    def test_autolink_is_an_osc8_hyperlink(self):
        rows = markdown.render("visit https://example.com now", 80)
        assert any("\x1b]8;;https://example.com\x1b\\" in r for r in rows)


class TestTable:
    def test_table_renders_cells(self):
        md = "| A | B |\n|---|---|\n| 1 | 2 |"
        rows = plain_rows(md)
        text = "\n".join(rows)
        assert "A" in text and "1" in text and "2" in text

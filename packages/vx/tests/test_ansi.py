"""ANSI width, clipping, wrapping and ctx-markup rendering."""

import re

from vx.tui import ansi

_STRIP = re.compile(r"\x1b\[[0-9;]*m")


def plain(text: str) -> str:
    return _STRIP.sub("", ansi.strip_ansi(text))


class TestStripAndWidth:
    def test_strip_removes_sgr(self):
        assert ansi.strip_ansi("\x1b[31mred\x1b[0m") == "red"

    def test_width_ignores_escapes(self):
        assert ansi.width("\x1b[1;31mhi\x1b[0m") == 2

    def test_wide_chars_count_two(self):
        assert ansi.width("ab") == 2
        assert ansi.width("\u4e16\u754c") == 4
        assert ansi.width("a") == 1

    def test_combining_char_zero_width(self):
        assert ansi.char_width("́") == 0


class TestClip:
    def test_clip_plain(self):
        assert ansi.clip("hello", 3) == "hel"

    def test_clip_keeps_ansi(self):
        out = ansi.clip("\x1b[31mhello\x1b[0m", 3)
        assert "\x1b[31m" in out
        assert plain(out) == "hel"

    def test_clip_zero(self):
        assert ansi.clip("hello", 0) == ""

    def test_clip_ellipsis(self):
        assert ansi.clip_ellipsis("hello", 4) == "hel…"
        assert ansi.clip_ellipsis("hi", 10) == "hi"
        assert ansi.clip_ellipsis("hello", 1) == "…"


class TestPad:
    def test_pad_to_width(self):
        assert ansi.pad("ab", 5) == "ab   "

    def test_pad_noop_when_wide(self):
        assert ansi.pad("hello", 3) == "hello"


class TestWrap:
    def test_wrap_words(self):
        rows = ansi.wrap("one two three four", 9)
        assert [plain(r) for r in rows] == ["one two", "three", "four"]

    def test_wrap_keeps_newlines(self):
        rows = ansi.wrap("a\nb", 10)
        assert [plain(r) for r in rows] == ["a", "b"]

    def test_wrap_empty_paragraph(self):
        assert ansi.wrap("a\n\nb", 10) == ["a", "", "b"]

    def test_wrap_preserves_sgr_across_rows(self):
        rows = ansi.wrap("\x1b[31mone two three\x1b[0m", 8)
        assert all("\x1b[31m" in r for r in rows[:-1])
        assert "\x1b[0m" in rows[-1]

    def test_hard_wrap_splits_long_word(self):
        rows = ansi.hard_wrap("abcdefghij", 4)
        assert [plain(r) for r in rows] == ["abcd", "efgh", "ij"]

    def test_hard_wrap_respects_newlines(self):
        rows = ansi.hard_wrap("ab\ncd", 10)
        assert [plain(r) for r in rows] == ["ab", "cd"]


class TestMarkupToAnsi:
    def test_plain_passthrough(self):
        assert ansi.markup_to_ansi("hello") == "hello"

    def test_dim_tag(self):
        out = ansi.markup_to_ansi("[dim]x[/dim]")
        assert "\x1b[" in out and "x" in out

    def test_unmatched_close_is_literal(self):
        out = ansi.markup_to_ansi("a[/nope]b")
        assert "[/nope]" in out

    def test_hex_color_tag_is_literal(self):
        # _TAG_RE requires names to start with a letter, so [#rrggbb] never
        # parses and the hex branch in _sgr_for_tag is unreachable today.
        out = ansi.markup_to_ansi("[#ff0000]red[/#ff0000]")
        assert out == "[#ff0000]red[/#ff0000]"


class TestHighlightMatches:
    def test_wraps_a_plain_span(self):
        out = ansi.highlight_matches("hello world", [(6, 11)])
        assert out == "hello \x1b[7mworld\x1b[27m"

    def test_keeps_existing_sgr(self):
        out = ansi.highlight_matches("\x1b[31mhello\x1b[0m", [(0, 5)])
        assert out.startswith("\x1b[7m")
        assert "\x1b[31m" in out and "\x1b[0m" in out
        assert plain(out) == "hello"

    def test_two_spans(self):
        out = ansi.highlight_matches("ab cd", [(0, 2), (3, 5)])
        assert out == "\x1b[7mab\x1b[27m \x1b[7mcd\x1b[27m"


class TestOsc8:
    def test_link_is_zero_width(self):
        out = ansi.osc8("https://example.com", "docs")
        assert ansi.width(out) == 4
        assert ansi.strip_ansi(out) == "docs"

    def test_link_at_finds_uri(self):
        out = ansi.osc8("https://example.com", "docs")
        for col in range(4):
            assert ansi.link_at(out, col) == "https://example.com"
        assert ansi.link_at(out, 4) is None

    def test_link_at_ignores_plain_text(self):
        assert ansi.link_at("plain", 2) is None

    def test_empty_url_falls_back_to_text(self):
        assert ansi.osc8("\x1b", "text") == "text"

"""Footer helpers: composer wrapping, model labels, elapsed time, menus."""

import re

from vx.tui import footer

_STRIP = re.compile(r"\x1b\[[0-9;]*m")


def plain(text: str) -> str:
    return _STRIP.sub("", text)


class TestWrapInput:
    def test_short_text_one_row(self):
        rows = footer.wrap_input("hello", 20)
        assert rows == [(0, 5)]

    def test_newline_splits(self):
        rows = footer.wrap_input("ab\ncd", 20)
        assert rows == [(0, 2), (3, 5)]

    def test_wraps_at_width(self):
        rows = footer.wrap_input("aaaabbbb", 6)  # usable = cols - 2 = 4
        assert len(rows) == 2
        assert rows == [(0, 4), (4, 8)]


class TestCursorVisual:
    def test_start_of_text(self):
        row, col = footer.cursor_visual("hi", 0, 20)
        assert (row, col) == (0, 2)

    def test_after_chars(self):
        row, col = footer.cursor_visual("hi", 2, 20)
        assert (row, col) == (0, 4)

    def test_second_row(self):
        row, _col = footer.cursor_visual("ab\ncd", 4, 20)
        assert row == 1


class TestComposerWindow:
    def test_fits_returns_zero_scroll(self):
        total, _caret, first = footer.composer_window("hi", 1, 20, 3)
        assert (total, _caret, first) == (1, 0, 0)

    def test_overflow_scrolls_to_caret(self):
        text = "\n".join("x" for _ in range(10))
        total, _caret, first = footer.composer_window(text, len(text) - 1, 20, 3)
        assert total == 10
        assert first > 0


class TestCompactModel:
    def test_strips_provider_prefix(self):
        assert footer.compact_model("anthropic/claude-opus-4") == "opus 4"

    def test_sonnet_and_haiku(self):
        assert footer.compact_model("openai/claude-sonnet-4") == "sonnet 4"
        assert footer.compact_model("x/claude-haiku-3") == "haiku 3"

    def test_unknown_passthrough(self):
        assert footer.compact_model("openai/gpt-5") == "gpt-5"


class TestElapsedNote:
    def test_seconds(self):
        assert footer.elapsed_note(7) == "7s"

    def test_minutes(self):
        assert footer.elapsed_note(80) == "1m 20s"

    def test_hours(self):
        assert footer.elapsed_note(7500) == "2h 5m"


class TestMenuWindow:
    def test_small_list_no_scroll(self):
        assert footer.menu_window(3, 0, 6) == (0, 3)

    def test_selected_scrolls(self):
        start, end = footer.menu_window(20, 15, 6)
        assert start <= 15 < end
        assert end - start == 6

    def test_end_clamped(self):
        _start, end = footer.menu_window(20, 19, 6)
        assert end == 20


class TestHintRow:
    def test_contains_segments(self):
        row = footer.hint_row(
            cols=80,
            permission="auto",
            model="anthropic/claude-opus-4",
            effort="high",
            context_used=12000,
            context_total=200000,
            workspace="/tmp/proj",
            branch="main",
        )
        text = plain(row)
        assert "auto" in text
        assert "opus 4" in text
        assert "high" in text
        assert "main" in text

    def test_width_matches_cols(self):
        row = footer.hint_row(
            cols=60,
            permission="",
            model="m",
            effort="",
            context_used=0,
            context_total=None,
            workspace="/w",
        )
        from vx.tui import ansi

        assert ansi.width(row) == 60

    def test_hint_has_no_unseen_text(self):
        row = footer.hint_row(
            cols=100,
            permission="auto",
            model="",
            effort="",
            context_used=0,
            context_total=None,
            workspace="",
        )
        assert "new lines" not in plain(row)

    def test_custom_composer_prompt(self):
        rows, caret_row, caret_col = footer.composer_rows("hi", 2, 20, 1, prompt="⌕ ")
        assert rows[0].startswith("⌕ ")
        assert (caret_row, caret_col) == (0, 4)


class TestJumpToLatestPill:
    def test_label_and_fill(self):
        row = footer.jump_to_latest_pill(80)
        assert "↓ Jump to latest message Ctrl+End" in plain(row)
        assert "\x1b[48;" in row

    def test_is_centred(self):
        from vx.tui import ansi

        row = footer.jump_to_latest_pill(60)
        visible = ansi.strip_ansi(row)
        start = visible.index("↓")
        end = start + len("↓ Jump to latest message Ctrl+End")
        assert abs(start - (60 - end)) <= 1

    def test_is_clickable(self):
        from vx.tui import ansi

        row = footer.jump_to_latest_pill(80)
        assert ansi.link_at(row, ansi.width(row) // 2) == footer.JUMP_TO_LATEST_URI

    def test_narrow_terminal_clips(self):
        from vx.tui import ansi

        row = footer.jump_to_latest_pill(20)
        assert ansi.width(row) == 20

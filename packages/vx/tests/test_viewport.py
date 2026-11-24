"""Viewport: block rows, spacing, scrolling and the history limit."""

from dataclasses import dataclass

from vx.tui import ansi, blocks
from vx.tui.viewport import Viewport


@dataclass
class StubBlock(blocks.Block):
    kind: str = "notice"
    lines: list[str] | None = None

    def rows(self, width: int) -> list[str]:
        return list(self.lines or ["x"])


class TestBasics:
    def test_empty_viewport(self):
        v = Viewport()
        assert v.lines(80) == []
        assert v.total(80) == 0
        assert v.tail() is None

    def test_add_and_tail(self):
        v = Viewport()
        b = StubBlock(lines=["hello"])
        v.add(b)
        assert v.tail() is b
        assert v.lines(80) == ["hello"]

    def test_clear(self):
        v = Viewport()
        v.add(StubBlock())
        v.clear()
        assert v.lines(80) == []
        assert v.tail() is None


class TestVisible:
    def test_short_content_padded(self):
        v = Viewport()
        v.add(StubBlock(lines=["a", "b"]))
        rows = v.visible(80, 6)
        assert len(rows) == 6  # body=5 + trailing blank
        assert rows[0] == "a" and rows[1] == "b"

    def test_overflow_keeps_pinned_banner(self):
        v = Viewport()
        v.add(blocks.Welcome(version="1"))  # pinned first block
        for i in range(30):
            v.add(StubBlock(kind="assistant", lines=[f"row{i}"]))
        rows = v.visible(80, 8)
        assert len(rows) == 8
        # First row is the pinned welcome banner.
        assert "𝒗x" in rows[0] or "v1" in rows[0]

    def test_scroll_moves_window(self):
        v = Viewport()
        for i in range(30):
            v.add(StubBlock(kind="assistant", lines=[f"row{i}"]))
        before = v.visible(80, 8)
        v.scroll_by(5, 80, 8)
        after = v.visible(80, 8)
        assert before != after

    def test_scroll_clamped(self):
        v = Viewport()
        for i in range(10):
            v.add(StubBlock(kind="assistant", lines=[f"r{i}"]))
        v.scroll_by(-999, 80, 6)
        assert v.scroll >= 0
        v.scroll_by(999, 80, 6)
        assert v.scroll <= v._max_scroll(80, 6)

    def test_page_steps_a_screen_at_a_time(self):
        v = Viewport()
        for i in range(60):
            v.add(StubBlock(kind="assistant", lines=[f"row{i}"]))
        v.visible(80, 10)
        v.page(1, 80, 10)
        assert v.scroll == 8  # height - 2

    def test_new_rows_do_not_yank_the_reader(self):
        """A block arriving mid-read must not scroll the window back down."""
        v = Viewport()
        for i in range(40):
            v.add(StubBlock(kind="assistant", lines=[f"row{i}"]))
        v.page(1, 80, 10)
        v.visible(80, 10)
        seen = v.visible(80, 10)
        v.add(StubBlock(kind="assistant", lines=["late1", "late2", "late3"]))
        after = v.visible(80, 10)
        assert after == seen


class TestHistoryLimit:
    def test_limit_prunes_oldest(self):
        v = Viewport()
        v.set_history_limit(5)
        for i in range(20):
            v.add(StubBlock(kind="assistant", lines=[f"row{i}"]))
        rows = v.lines(80)
        assert len(rows) <= 6  # 5 + fold marker
        assert v.dropped_rows > 0
        assert any(isinstance(b, blocks.HistoryFold) for b in v.blocks)

    def test_zero_keeps_everything(self):
        v = Viewport()
        v.set_history_limit(0)
        for i in range(20):
            v.add(StubBlock(kind="assistant", lines=[f"row{i}"]))
        assert v.total(80) >= 20

    def test_welcome_never_pruned(self):
        v = Viewport()
        v.set_history_limit(3)
        v.add(blocks.Welcome(version="1"))
        for i in range(20):
            v.add(StubBlock(kind="assistant", lines=[f"row{i}"]))
        assert isinstance(v.blocks[0], blocks.Welcome)


class TestFollowTail:
    def test_follows_at_the_bottom(self):
        v = Viewport()
        for i in range(40):
            v.add(StubBlock(kind="assistant", lines=[f"row{i}"]))
        v.visible(80, 10)
        v.add(StubBlock(kind="assistant", lines=["late"]))
        v.visible(80, 10)
        assert v.follow and v.scroll == 0 and v.unseen == 0

    def test_scrolled_away_counts_new_rows(self):
        v = Viewport()
        for i in range(40):
            v.add(StubBlock(kind="assistant", lines=[f"row{i}"]))
        v.visible(80, 10)
        v.page(1, 80, 10)
        v.add(StubBlock(kind="assistant", lines=["a", "b", "c"]))
        v.visible(80, 10)
        assert not v.follow
        assert v.unseen == 3

    def test_bottom_resets_follow_and_unseen(self):
        v = Viewport()
        for i in range(40):
            v.add(StubBlock(kind="assistant", lines=[f"row{i}"]))
        v.visible(80, 10)
        v.page(1, 80, 10)
        v.add(StubBlock(kind="assistant", lines=["late"]))
        v.visible(80, 10)
        v.bottom()
        assert v.follow and v.scroll == 0 and v.unseen == 0


class TestSearch:
    def test_matches_are_case_insensitive_plain_spans(self):
        v = Viewport()
        v.add(StubBlock(lines=["Hello world"]))
        v.set_search("WORLD", 80)
        assert v.search_matches == [(0, 6, 11)]

    def test_highlight_marks_the_match_without_changing_text(self):
        v = Viewport()
        v.add(StubBlock(lines=["\x1b[31mHello world\x1b[0m"]))
        v.set_search("world", 80)
        out = v.highlight(v.visible(80, 6))
        assert "\x1b[7m" in out[0] and "\x1b[27m" in out[0]
        assert "\x1b[31m" in out[0]
        assert ansi.strip_ansi(out[0]) == "Hello world"

    def test_next_cycles_matches(self):
        v = Viewport()
        v.add(StubBlock(lines=["alpha", "alpha"]))
        v.set_search("alpha", 80)
        assert len(v.search_matches) == 2
        v.search_next(1, 80, 10)
        assert v.search_index == 1
        v.search_next(1, 80, 10)
        assert v.search_index == 0

    def test_empty_query_clears_matches(self):
        v = Viewport()
        v.add(StubBlock(lines=["alpha"]))
        v.set_search("alpha", 80)
        v.set_search("", 80)
        assert v.search_matches == []

"""Viewport: block rows, spacing, scrolling and the history limit."""

from dataclasses import dataclass

from vx.tui import blocks
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

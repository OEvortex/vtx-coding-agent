"""Canvas painter: absolute row writes and changed-row diffing."""

from vx.tui.canvas import Painter


class TestPainter:
    def test_first_paint_writes_all_rows(self):
        p = Painter()
        out = p.paint(["a", "b", "c"], 10)
        # Three absolute-position writes plus cursor hide.
        assert out.count("\x1b[") >= 3
        assert "\x1b[?25l" in out  # no cursor -> hidden

    def test_second_identical_paint_is_minimal(self):
        p = Painter()
        p.paint(["a", "b"], 10)
        out = p.paint(["a", "b"], 10)
        # No row rewrites: only cursor control remains.
        assert "\x1b[1;1H" not in out
        assert "\x1b[?25l" in out

    def test_changed_row_rewritten(self):
        p = Painter()
        p.paint(["a", "b"], 10)
        out = p.paint(["a", "CHANGED"], 10)
        assert "\x1b[2;1H" in out
        assert "CHANGED" in out

    def test_resize_forces_full_repaint(self):
        p = Painter()
        p.paint(["a", "b"], 10)
        out = p.paint(["a", "b"], 20)
        assert "\x1b[1;1H" in out

    def test_cursor_position_emitted(self):
        p = Painter()
        out = p.paint(["row"], 10, cursor=(0, 2))
        assert "\x1b[1;3H" in out
        assert "\x1b[?25h" in out

    def test_rows_clipped_to_cols(self):
        p = Painter()
        out = p.paint(["x" * 50], 10)
        # Row content clipped to 10 cells then padded.
        assert "x" * 50 not in out

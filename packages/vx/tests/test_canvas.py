"""Canvas painter: absolute row writes and changed-row diffing."""

from vx.tui.canvas import Painter


class TestPainter:
    def test_first_paint_writes_all_rows(self):
        p = Painter()
        out = p.paint(["a", "b", "c"], 10)
        # Three absolute-position writes; the caret is already hidden by
        # Terminal.enter(), so hiding it again would be a second mode change.
        assert out.count("\x1b[1;1H") == 1
        assert out.count(";1H") == 3
        assert "\x1b[?25l" not in out

    def test_second_identical_paint_writes_nothing(self):
        p = Painter()
        p.paint(["a", "b"], 10)
        # A frame that changed nothing is not a frame: an idle screen must cost
        # the terminal zero bytes, or every tick is a composite on a GPU one.
        assert p.paint(["a", "b"], 10) == ""

    def test_an_idle_caret_is_not_re_sent(self):
        p = Painter()
        p.paint(["a"], 10, cursor=(0, 2))
        assert p.paint(["a"], 10, cursor=(0, 2)) == ""
        moved = p.paint(["a"], 10, cursor=(0, 5))
        assert "\x1b[1;6H" in moved
        assert moved.count("\x1b[") == 3  # sync on, move, sync off

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

    def test_a_frame_is_an_atomic_repaint(self):
        p = Painter()
        out = p.paint(["a"], 10)
        # DEC 2026 around the whole frame: a GPU terminal presents the finished
        # rows instead of every cursor move as it arrives.
        assert out.startswith("\x1b[?2026h")
        assert out.endswith("\x1b[?2026l")
        assert out.count("\x1b[?2026h") == 1

    def test_cursor_visibility_is_a_transition(self):
        # Shown once when a caret appears, hidden once when it goes away, and
        # never re-asserted in between: a DECSET nobody acted on is still a
        # repaint on a GPU terminal.
        p = Painter()
        assert "\x1b[?25h" in p.paint(["a"], 10, cursor=(0, 0))
        assert "\x1b[?25h" not in p.paint(["b"], 10, cursor=(0, 1))
        assert "\x1b[?25l" in p.paint(["b"], 10)
        assert "\x1b[?25l" not in p.paint(["c"], 10)

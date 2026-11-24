"""Terminal: mouse reporting is always on, alt-screen lifecycle constants."""

from vx.tui import terminal


class TestMouseReporting:
    def test_alternate_screen_enables_mouse(self):
        # enter() writes _ALTERNATE_SCREEN_ON; the wheel must be live from the
        # first frame, with no pref to toggle it.
        assert terminal._MOUSE_ON in terminal._ALTERNATE_SCREEN_ON

    def test_exit_disables_mouse(self):
        assert terminal._MOUSE_OFF in terminal._ALTERNATE_SCREEN_OFF

    def test_sgr_mouse_coordinates(self):
        assert "?1006h" in terminal._MOUSE_ON  # SGR 1006 for x;y coords

    def test_signal_cleanup_restores_terminal(self):
        called = False

        class DummyTerminal(terminal.Terminal):
            def __init__(self):
                pass

            def exit(self):
                nonlocal called
                called = True

        dummy = DummyTerminal()
        terminal._ACTIVE_TERMINALS.add(dummy)
        try:
            terminal._cleanup_terminals()
            assert called is True
        finally:
            terminal._ACTIVE_TERMINALS.discard(dummy)

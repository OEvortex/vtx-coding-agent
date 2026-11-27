"""Terminal: mouse reporting is always on, alt-screen lifecycle constants."""

import os
import pty

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

    def test_autowrap_is_off_while_the_frame_owns_the_screen(self):
        # Every painted row is padded to the terminal width, and a GPU-rendered
        # terminal that resolves the pending-wrap state by scrolling turns a
        # repaint into a scroll. Off on enter, back on before leaving.
        assert terminal._NO_WRAP_OFF in terminal._ALTERNATE_SCREEN_ON
        assert terminal._NO_WRAP_ON in terminal._ALTERNATE_SCREEN_OFF
        assert terminal._ALTERNATE_SCREEN_OFF.index(
            terminal._NO_WRAP_ON
        ) < terminal._ALTERNATE_SCREEN_OFF.index("?1049l")

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


def test_enter_keeps_the_tty_blocking():
    """stdin and stdout share one fd; O_NONBLOCK would drop whole frames."""
    master, slave = pty.openpty()

    class Stdin:
        def fileno(self):
            return slave

    class Stdout:
        def write(self, data):
            return len(data)

        def flush(self):
            pass

    try:
        term = terminal.Terminal(stdin=Stdin(), stdout=Stdout())
        term.enter()
        try:
            assert os.get_blocking(slave) is True
        finally:
            term.exit()
    finally:
        os.close(master)
        os.close(slave)

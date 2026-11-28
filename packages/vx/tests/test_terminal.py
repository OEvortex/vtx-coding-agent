"""Terminal: mouse reporting is always on, alt-screen lifecycle constants."""

import os
import pty

import pytest
from vx.tui import terminal
from vx.tui.terminal import Terminal, TerminalError


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


def _pipe_stdin():
    """A stdin-like object backed by a pipe (never a tty)."""
    read_fd, write_fd = os.pipe()

    class Stdin:
        def fileno(self):
            return read_fd

    return Stdin(), read_fd, write_fd


def test_enter_rejects_non_tty_stdin():
    stdin, read_fd, write_fd = _pipe_stdin()
    try:
        term = Terminal(stdin=stdin)
        with pytest.raises(TerminalError, match="not a terminal"):
            term.enter()
        assert term._running is False
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_read_available_latches_eof_instead_of_polling_forever():
    stdin, read_fd, write_fd = _pipe_stdin()
    try:
        term = Terminal(stdin=stdin, stdout=None)
        # Nothing pending: empty, not EOF.
        assert term.read_available() == b""
        assert term.eof is False
        # Writer closes: the read end reports EOF exactly once, then latches.
        os.close(write_fd)
        write_fd = -1
        assert term.read_available() == b""
        assert term.eof is True
        # Subsequent calls short-circuit without touching the fd.
        assert term.read_available() == b""
        assert term.eof is True
    finally:
        os.close(read_fd)
        if write_fd != -1:
            os.close(write_fd)


def test_eof_on_pty_hangup_via_eio():
    """Closing the pty master makes the slave read fail with EIO on Linux."""
    master, slave = pty.openpty()

    class Stdin:
        def fileno(self):
            return slave

    try:
        term = Terminal(stdin=Stdin(), stdout=None)
        os.close(master)
        # Drain whatever the hangup delivered, then the next read reports EIO.
        for _ in range(3):
            term.read_available()
        assert term.eof is True
    finally:
        os.close(slave)

"""Raw terminal access: bracketed keys, window size, alternate screen.

Stdlib only (termios/tty/select/ioctl). No third-party imports.
"""

from __future__ import annotations

import os
import select
import sys
import termios
import tty
from typing import Any

#: Button events (clicks and the wheel) plus SGR coordinates. Reporting the
#: pointer is what turns off the terminal's own drag-select, so it is off
#: unless wheel scrolling was asked for.
_MOUSE_ON = "\x1b[?1000h\x1b[?1006h"
_MOUSE_OFF = "\x1b[?1006l\x1b[?1000l"

_ALTERNATE_SCREEN_ON = "\x1b[?1049h\x1b[?25l\x1b[?2004h"
_ALTERNATE_SCREEN_OFF = _MOUSE_OFF + "\x1b[?2004l\x1b[?25h\x1b[?1049l"


class Terminal:
    """Owns the tty in raw mode while the app runs."""

    def __init__(self, stdin: Any = None, stdout: Any = None) -> None:
        self.stdin = stdin or sys.stdin
        self.stdout = stdout or sys.stdout
        self._fd = self.stdin.fileno()
        self._saved_attrs: Any = None
        self._running = False
        self._mouse = False

    # -- lifecycle ---------------------------------------------------------

    def enter(self) -> None:
        self._saved_attrs = termios.tcgetattr(self._fd)
        tty.setraw(self._fd)
        # Keep IUTF8-ish decoding simple: raw mode still delivers UTF-8 bytes.
        self._running = True
        os.set_blocking(self._fd, False)
        self.write(_ALTERNATE_SCREEN_ON)
        self.write("\x1b[2J\x1b[H")

    def set_mouse(self, enabled: bool) -> None:
        """Hand the pointer to the app (wheel scrolling) or back to the terminal.

        With reporting on, the terminal sends clicks and wheel turns here and
        stops doing its own drag-select; shift+drag is the terminal's own
        escape hatch. With reporting off, drag selects text and copies as
        usual, and the transcript scrolls with the page keys.
        """
        if enabled == self._mouse:
            return
        self._mouse = enabled
        if self._running:
            self.write(_MOUSE_ON if enabled else _MOUSE_OFF)

    def exit(self) -> None:
        if not self._running:
            return
        self._running = False
        self.write(_ALTERNATE_SCREEN_OFF)
        if self._saved_attrs is not None:
            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._saved_attrs)
            self._saved_attrs = None
        os.set_blocking(self._fd, True)

    def __enter__(self) -> Terminal:
        self.enter()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.exit()

    # -- io ----------------------------------------------------------------

    def write(self, data: str) -> None:
        try:
            self.stdout.write(data)
            self.stdout.flush()
        except (BrokenPipeError, OSError):
            pass

    def read_available(self, max_bytes: int = 65536) -> bytes:
        """Non-blocking read of whatever is pending on stdin."""
        try:
            r, _, _ = select.select([self._fd], [], [], 0)
            if not r:
                return b""
            return os.read(self._fd, max_bytes)
        except (BlockingIOError, InterruptedError):
            return b""
        except OSError:
            return b""

    # -- size ---------------------------------------------------------------

    def size(self) -> tuple[int, int]:
        """(rows, cols) of the terminal."""
        try:
            import fcntl
            import struct
            import termios as _t

            data = fcntl.ioctl(self._fd, _t.TIOCGWINSZ, b"\x00" * 8)
            rows, cols = struct.unpack("HH", data[:4])
            if rows and cols:
                return rows, cols
        except (ImportError, OSError):
            pass
        return int(os.environ.get("LINES", 24)), int(os.environ.get("COLUMNS", 80))

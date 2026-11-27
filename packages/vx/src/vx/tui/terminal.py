"""Raw terminal access: bracketed keys, window size, alternate screen.

Stdlib only (termios/tty/select/ioctl). No third-party imports.
"""

from __future__ import annotations

import atexit
import contextlib
import os
import select
import signal
import sys
import termios
import tty
from typing import Any

#: Button events (clicks and the wheel) plus SGR coordinates. Always on:
#: reporting the pointer is what lets the wheel scroll the transcript, and
#: shift+drag remains the terminal's own drag-select escape hatch.
_MOUSE_ON = "\x1b[?1000h\x1b[?1006h"
_MOUSE_OFF = "\x1b[?1006l\x1b[?1000l"

#: Autowrap off for the session: every frame writes rows padded to the full
#: width, and on a terminal with wrapping still on, the last cell of such a row
#: leaves the cursor in the pending-wrap state. A GPU-rendered terminal that
#: resolves that state by scrolling turns a repaint into a scroll.
_NO_WRAP_OFF = "\x1b[?7l"
_NO_WRAP_ON = "\x1b[?7h"

_ALTERNATE_SCREEN_ON = "\x1b[?1049h\x1b[?25l\x1b[?2004h" + _MOUSE_ON + _NO_WRAP_OFF
_ALTERNATE_SCREEN_OFF = _MOUSE_OFF + "\x1b[?2004l" + "\x1b[?25h" + _NO_WRAP_ON + "\x1b[?1049l"

_ACTIVE_TERMINALS: set[Terminal] = set()
_SIGNALS_HOOKED = False
_OLD_SIGNAL_HANDLERS: dict[int, Any] = {}


def _cleanup_terminals() -> None:
    for term in list(_ACTIVE_TERMINALS):
        term.exit()


def _handle_signal(sig: int, frame: Any) -> None:
    _cleanup_terminals()
    old_handler = _OLD_SIGNAL_HANDLERS.get(sig)
    if callable(old_handler):
        old_handler(sig, frame)
    else:
        # Re-raise or default terminate with proper signal exit code
        signal.signal(sig, signal.SIG_DFL)
        os.kill(os.getpid(), sig)


def _hook_signals() -> None:
    global _SIGNALS_HOOKED
    if _SIGNALS_HOOKED:
        return
    _SIGNALS_HOOKED = True
    atexit.register(_cleanup_terminals)
    for sig in (signal.SIGTERM, signal.SIGHUP):
        with contextlib.suppress(ValueError, OSError):
            _OLD_SIGNAL_HANDLERS[sig] = signal.signal(sig, _handle_signal)


class Terminal:
    """Owns the tty in raw mode while the app runs."""

    def __init__(self, stdin: Any = None, stdout: Any = None) -> None:
        self.stdin = stdin or sys.stdin
        self.stdout = stdout or sys.stdout
        self._fd = self.stdin.fileno()
        self._saved_attrs: Any = None
        self._running = False

    # -- lifecycle ---------------------------------------------------------

    def enter(self) -> None:
        _hook_signals()
        self._saved_attrs = termios.tcgetattr(self._fd)
        tty.setraw(self._fd)
        # Keep IUTF8-ish decoding simple: raw mode still delivers UTF-8 bytes.
        self._running = True
        _ACTIVE_TERMINALS.add(self)
        # Do NOT make the fd non-blocking: in an interactive shell stdin and
        # stdout share one terminal file description, so O_NONBLOCK would also
        # apply to stdout. A full output buffer then makes flush() raise and
        # the whole frame is silently dropped, leaving torn, half-painted
        # screens. select() below already guarantees a read cannot block.
        self.write(_ALTERNATE_SCREEN_ON)
        self.write("\x1b[2J\x1b[H")

    def exit(self) -> None:
        if not self._running:
            return
        self._running = False
        _ACTIVE_TERMINALS.discard(self)
        self.write(_ALTERNATE_SCREEN_OFF)
        if self._saved_attrs is not None:
            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._saved_attrs)
            self._saved_attrs = None

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

"""Frame painting: absolute row writes with a changed-row diff.

fx rewrites only the rows that changed. Writing each row at an absolute
position (never a newline) means the frame can never scroll the screen, which
is what keeps the composer glued to the bottom row.

Two things matter beyond correctness, and they are what a GPU-rendered terminal
(Ghostty, Kitty, WezTerm, Alacritty, a VTE with the GL renderer) punishes:

* **Quiet frames cost nothing.** A frame that changed nothing returns ``""`` so
  the caller writes nothing at all. Emitting a cursor move every tick was 33
  damage events a second on an idle session, and every damage event on those
  terminals is a composite.
* **A frame is one atomic repaint.** The rows are wrapped in synchronized output
  (DEC 2026) so the terminal shows the finished frame instead of the half-written
  one. The caret is placed in the same frame as the rows it belongs to: a
  repaint that left it alone would hand the cursor to whichever row was written
  last.

Autowrap is turned off for the session by :class:`vx.tui.terminal.Terminal`, so a
row padded to the full width ends in a plain last cell rather than the terminal's
pending-wrap state.
"""

from __future__ import annotations

from functools import lru_cache

from vx.tui import ansi

#: DEC 2026: the terminal buffers everything between these and presents one
#: frame. Terminals that do not know the mode ignore it, per the spec.
_SYNC_ON = "\x1b[?2026h"
_SYNC_OFF = "\x1b[?2026l"

_HIDE_CURSOR = "\x1b[?25l"
_SHOW_CURSOR = "\x1b[?25h"


@lru_cache(maxsize=16384)
def _fit(row: str, cols: int) -> str:
    """One full-width painted row; memoized because frames repeat rows.

    A quiet frame still walks every row, and ``clip``+``pad`` each measure the
    text -- twice for styled rows. The frame's rows are the same strings frame
    after frame, so the measurement is paid once per distinct row, not per
    frame. The cache is keyed on the row itself, so a theme change (which
    rewrites the escapes inside the row) misses rather than serving stale
    styling.
    """
    if not row:
        return " " * cols
    return ansi.pad(ansi.clip(row, cols), cols)


class Painter:
    def __init__(self) -> None:
        self._last: list[str] = []
        self._rows = 0
        self._cols = 0
        self._cursor: tuple[int, int] | None = None
        #: Whether the caret is currently hidden, so visibility is a transition
        #: rather than a state re-asserted every frame. It starts hidden because
        #: ``Terminal.enter()`` hides it when it switches to the alternate
        #: screen: the first frame ends the state the terminal is already in.
        self._hidden = True

    def reset(self) -> None:
        self._last = []
        self._rows = 0
        self._cols = 0
        self._cursor = None
        self._hidden = True

    def paint(self, rows: list[str], cols: int, cursor: tuple[int, int] | None = None) -> str:
        """The bytes for one frame, or ``""`` when the screen is already current."""
        height = len(rows)
        full = height != self._rows or cols != self._cols or not self._last
        body: list[str] = []
        padded: list[str] = []
        for index, row in enumerate(rows):
            # Always emit exactly one full-width row. A separate 2K first would
            # briefly blank the line, and a screenshot (or a busy terminal)
            # catches that gap as a solid bar through the picture.
            clipped = _fit(row, cols)
            padded.append(clipped)
            previous = self._last[index] if index < len(self._last) else None
            if full or previous != clipped:
                body.append(f"\x1b[{index + 1};1H")
                body.append(clipped)

        if cursor is not None:
            # A row is written padded to the full width, so every row write
            # leaves the cursor on that row's last cell. The caret has to be
            # placed again whenever anything was repainted -- not only when it
            # moved -- or it is dragged along with the rows and ends up sitting
            # on the last cell of the last dirty row, which is a solid block
            # over the footer's last character.
            cursor = (min(cursor[0], height - 1), min(cursor[1], max(0, cols - 1)))

        tail: list[str] = []
        if cursor is None:
            if not self._hidden:
                tail.append(_HIDE_CURSOR)
                self._hidden = True
        else:
            if self._hidden:
                tail.append(_SHOW_CURSOR)
                self._hidden = False
            if body or self._cursor != cursor:
                tail.append(f"\x1b[{cursor[0] + 1};{cursor[1] + 1}H")

        self._last = padded
        self._rows = height
        self._cols = cols
        self._cursor = cursor

        if not body and not tail:
            return ""
        return _SYNC_ON + "".join(body) + "".join(tail) + _SYNC_OFF

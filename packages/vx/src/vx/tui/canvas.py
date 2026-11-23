"""Frame painting: absolute row writes with a changed-row diff.

fx rewrites only the rows that changed. Writing each row at an absolute
position (never a newline) means the frame can never scroll the screen, which
is what keeps the composer glued to the bottom row.
"""

from __future__ import annotations

from vx.tui import ansi


class Painter:
    def __init__(self) -> None:
        self._last: list[str] = []
        self._rows = 0
        self._cols = 0
        self._cursor: tuple[int, int] | None = None

    def reset(self) -> None:
        self._last = []
        self._rows = 0
        self._cols = 0
        self._cursor = None

    def paint(self, rows: list[str], cols: int, cursor: tuple[int, int] | None = None) -> str:
        height = len(rows)
        parts: list[str] = []
        full = height != self._rows or cols != self._cols or not self._last
        for index, row in enumerate(rows):
            clipped = ansi.pad(ansi.clip(row, cols), cols) if row else ""
            previous = self._last[index] if index < len(self._last) else None
            if full or previous != clipped:
                parts.append(f"\x1b[{index + 1};1H")
                parts.append("\x1b[2K")
                parts.append(clipped)
        if cursor is not None:
            parts.append(f"\x1b[{cursor[0] + 1};{cursor[1] + 1}H")
            if self._cursor != cursor:
                parts.append("\x1b[?25h")
        else:
            parts.append("\x1b[?25l")
        self._last = [ansi.pad(ansi.clip(r, cols), cols) if r else "" for r in rows]
        self._rows = height
        self._cols = cols
        self._cursor = cursor
        return "".join(parts)
